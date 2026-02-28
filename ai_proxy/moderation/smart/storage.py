"""
Sample storage backed by RocksDB (via rocksdict).

Compatibility goals:
- Keep the existing SampleStorage API surface used by the project.
- Auto-migrate legacy SQLite data from `history.db` to `history.rocks`.
- After successful migration, rename SQLite file to `.bak`.
"""
from __future__ import annotations

import os
import random
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import orjson
from pydantic import BaseModel
from rocksdict import Rdict


def json_loads(s: str) -> dict:
    return orjson.loads(s)


def json_dumps(obj: dict) -> bytes:
    return orjson.dumps(obj, option=orjson.OPT_NON_STR_KEYS)


def json_dumps_text(obj: dict) -> str:
    return json_dumps(obj).decode("utf-8")


class Sample(BaseModel):
    """Moderation sample record."""

    id: Optional[int] = None
    text: str
    label: int  # 0=pass, 1=violation
    category: Optional[str] = None
    created_at: Optional[str] = None


# Global DB handle cache and per-db locks.
_rocks_dbs: Dict[str, Rdict] = {}
_rocks_locks: Dict[str, threading.RLock] = {}
_global_lock = threading.Lock()


def _rocks_path_from_db_path(db_path: str) -> str:
    p = Path(db_path)
    if p.suffix == ".db":
        return str(p.with_suffix(".rocks"))
    return str(p.with_name(p.name + ".rocks"))


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _rename_to_bak(path: Path) -> None:
    if not path.exists():
        return
    dst = _backup_path(path)
    if dst.exists():
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        dst = path.with_name(f"{path.name}.{ts}.bak")
    last_err: Optional[Exception] = None
    for _ in range(8):
        try:
            path.replace(dst)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.2)
    try:
        shutil.copy2(path, dst)
        print(f"[MIGRATION] WARNING: copied (not renamed) {path} -> {dst} due to lock: {last_err}")
        try:
            path.unlink()
        except Exception:
            pass
        return
    except Exception as copy_err:
        print(f"[MIGRATION] WARNING: failed to backup {path} -> {dst}: {copy_err}")


def _sample_key(sample_id: int) -> str:
    return f"sample:{sample_id:020d}"


def _text_latest_key(text_hash: str) -> str:
    return f"text_latest:{text_hash}"


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _parse_sample(raw: str) -> Sample:
    data = json_loads(raw)
    return Sample(
        id=data.get("id"),
        text=data.get("text", ""),
        label=data.get("label", 0),
        category=data.get("category"),
        created_at=data.get("created_at"),
    )


def _sample_to_json(sample: Sample, text_hash: str) -> str:
    payload = {
        "id": sample.id,
        "text": sample.text,
        "label": sample.label,
        "category": sample.category,
        "created_at": sample.created_at,
        "text_hash": text_hash,
    }
    return json_dumps_text(payload)


def _get_db_and_lock(rocks_path: str) -> Tuple[Rdict, threading.RLock]:
    with _global_lock:
        if rocks_path not in _rocks_dbs:
            _rocks_dbs[rocks_path] = Rdict(rocks_path)
            _rocks_locks[rocks_path] = threading.RLock()
        return _rocks_dbs[rocks_path], _rocks_locks[rocks_path]


def cleanup_pools() -> None:
    """Compatibility shim for old SQLite cleanup hook."""
    with _global_lock:
        for db in _rocks_dbs.values():
            try:
                db.close()
            except Exception:
                pass
        _rocks_dbs.clear()
        _rocks_locks.clear()


class SampleStorage:
    """Sample storage manager (RocksDB backend)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.rocks_path = _rocks_path_from_db_path(db_path)
        self._migrate_sqlite_if_needed()
        self.db, self._lock = _get_db_and_lock(self.rocks_path)
        self._init_meta()

    def _init_meta(self) -> None:
        with self._lock:
            if self.db.get("meta:next_id") is None:
                self.db["meta:next_id"] = "1"
            if self.db.get("meta:count") is None:
                self.db["meta:count"] = "0"
            if self.db.get("meta:count:0") is None:
                self.db["meta:count:0"] = "0"
            if self.db.get("meta:count:1") is None:
                self.db["meta:count:1"] = "0"

    def _migrate_sqlite_if_needed(self) -> None:
        sqlite_path = Path(self.db_path)
        rocks_path = Path(self.rocks_path)

        if not sqlite_path.exists():
            return
        if rocks_path.exists():
            # Already migrated. Keep behavior explicit and archive old SQLite.
            _rename_to_bak(sqlite_path)
            shm = sqlite_path.with_name(sqlite_path.name + "-shm")
            wal = sqlite_path.with_name(sqlite_path.name + "-wal")
            _rename_to_bak(shm)
            _rename_to_bak(wal)
            return

        temp_rocks = rocks_path.with_name(rocks_path.name + ".migrating")
        if temp_rocks.exists():
            import shutil

            shutil.rmtree(temp_rocks)

        print(f"[MIGRATION] SQLite -> RocksDB: {sqlite_path} -> {rocks_path}")
        temp_db = Rdict(str(temp_rocks))

        try:
            next_id = 1
            count_0 = 0
            count_1 = 0

            with sqlite3.connect(sqlite_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, text, label, category, created_at
                    FROM samples
                    ORDER BY id ASC
                    """
                )
                rows = cur.fetchall()

            for rid, text, label, category, created_at in rows:
                text = text or ""
                label = int(label or 0)
                sample = Sample(
                    id=int(rid),
                    text=text,
                    label=label,
                    category=category,
                    created_at=created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                text_hash = _hash_text(sample.text)
                temp_db[_sample_key(sample.id)] = _sample_to_json(sample, text_hash)
                temp_db[_text_latest_key(text_hash)] = str(sample.id)
                if sample.label == 0:
                    count_0 += 1
                else:
                    count_1 += 1
                next_id = max(next_id, sample.id + 1)

            temp_db["meta:next_id"] = str(next_id)
            temp_db["meta:count"] = str(count_0 + count_1)
            temp_db["meta:count:0"] = str(count_0)
            temp_db["meta:count:1"] = str(count_1)
        finally:
            temp_db.close()

        temp_rocks.replace(rocks_path)

        _rename_to_bak(sqlite_path)
        _rename_to_bak(sqlite_path.with_name(sqlite_path.name + "-shm"))
        _rename_to_bak(sqlite_path.with_name(sqlite_path.name + "-wal"))
        print(f"[MIGRATION] Done. SQLite backed up as .bak")

    def _next_id(self) -> int:
        return int(self.db.get("meta:next_id", "1"))

    def _get_count(self) -> int:
        return int(self.db.get("meta:count", "0"))

    def _get_label_count(self, label: int) -> int:
        return int(self.db.get(f"meta:count:{label}", "0"))

    def _set_counts(self, total: int, count_0: int, count_1: int) -> None:
        self.db["meta:count"] = str(total)
        self.db["meta:count:0"] = str(count_0)
        self.db["meta:count:1"] = str(count_1)

    def save_sample(self, text: str, label: int, category: Optional[str] = None):
        with self._lock:
            sid = self._next_id()
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            text_hash = _hash_text(text)
            sample = Sample(id=sid, text=text, label=label, category=category, created_at=created_at)
            self.db[_sample_key(sid)] = _sample_to_json(sample, text_hash)
            self.db[_text_latest_key(text_hash)] = str(sid)

            count_0 = self._get_label_count(0)
            count_1 = self._get_label_count(1)
            if label == 0:
                count_0 += 1
            else:
                count_1 += 1
            self._set_counts(count_0 + count_1, count_0, count_1)
            self.db["meta:next_id"] = str(sid + 1)

    def _load_sample_by_id(self, sid: int) -> Optional[Sample]:
        raw = self.db.get(_sample_key(sid))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return _parse_sample(raw)

    def load_samples(self, max_samples: int = 20000) -> List[Sample]:
        if max_samples <= 0:
            return []
        out: List[Sample] = []
        with self._lock:
            sid = self._next_id() - 1
            while sid > 0 and len(out) < max_samples:
                sample = self._load_sample_by_id(sid)
                if sample is not None:
                    out.append(sample)
                sid -= 1
        return out

    def load_random_samples(self, max_samples: int = 20000) -> List[Sample]:
        if max_samples <= 0:
            return []
        ids = self.get_sample_ids(self._get_count())
        if not ids:
            return []
        if len(ids) > max_samples:
            ids = random.sample(ids, max_samples)
        return self.load_by_ids(ids)

    def load_balanced_latest_samples(self, max_samples: int = 20000) -> List[Sample]:
        if max_samples <= 0:
            return []

        pass_count, violation_count = self.get_label_counts()
        target_per_label = max_samples // 2
        if target_per_label <= 0:
            return []

        pass_take = min(pass_count, target_per_label)
        violation_take = min(violation_count, target_per_label)

        print(f"[CappedLatest] 数据库样本分布: 正常={pass_count}, 违规={violation_count}")
        print(f"[CappedLatest] 每类最多取 {target_per_label} 个：正常取 {pass_take}，违规取 {violation_take}")

        pass_samples = self._load_samples_by_label(0, pass_take)
        violation_samples = self._load_samples_by_label(1, violation_take)

        combined = pass_samples + violation_samples
        random.shuffle(combined)
        print(f"[CappedLatest] 最终样本: 正常={len(pass_samples)}, 违规={len(violation_samples)}, 总计={len(combined)}")
        return combined

    def load_balanced_random_samples(self, max_samples: int = 20000) -> List[Sample]:
        if max_samples <= 0:
            return []

        pass_count, violation_count = self.get_label_counts()
        target_per_label = max_samples // 2
        if target_per_label <= 0:
            return []

        pass_take = min(pass_count, target_per_label)
        violation_take = min(violation_count, target_per_label)

        print(f"[CappedRandom] 数据库样本分布: 正常={pass_count}, 违规={violation_count}")
        print(f"[CappedRandom] 每类最多取 {target_per_label} 个：正常取 {pass_take}，违规取 {violation_take}")

        pass_ids = [s.id for s in self._load_samples_by_label(0, pass_count)]
        violation_ids = [s.id for s in self._load_samples_by_label(1, violation_count)]
        if len(pass_ids) > pass_take:
            pass_ids = random.sample(pass_ids, pass_take)
        if len(violation_ids) > violation_take:
            violation_ids = random.sample(violation_ids, violation_take)

        pass_samples = self.load_by_ids(pass_ids)
        violation_samples = self.load_by_ids(violation_ids)

        combined = pass_samples + violation_samples
        random.shuffle(combined)
        print(f"[CappedRandom] 最终样本: 正常={len(pass_samples)}, 违规={len(violation_samples)}, 总计={len(combined)}")
        return combined

    def load_balanced_samples(self, max_samples: int = 20000) -> List[Sample]:
        if max_samples <= 0:
            return []

        pass_count, violation_count = self.get_label_counts()
        if pass_count == 0 or violation_count == 0:
            print(f"[WARNING] 标签不平衡: 正常={pass_count}, 违规={violation_count}")
            print(f"[WARNING] 无法进行平衡采样，返回空列表")
            return []

        balanced_count = min(pass_count, violation_count)
        target_per_label = max_samples // 2
        if target_per_label > 0:
            balanced_count = min(balanced_count, target_per_label)

        if balanced_count == 0:
            print(f"[WARNING] 计算出的平衡数量为0，返回空列表")
            return []

        print(f"[BalancedSampling] 数据库样本分布: 正常={pass_count}, 违规={violation_count}")
        print(f"[BalancedSampling] 使用欠采样策略，每类抽取 {balanced_count} 个样本（不复制）")

        pass_samples = self._load_samples_by_label(0, pass_count)
        if len(pass_samples) > balanced_count:
            pass_samples = random.sample(pass_samples, balanced_count)
        print(f"[BalancedSampling] 正常样本: {len(pass_samples)} 个")

        violation_samples = self._load_samples_by_label(1, violation_count)
        if len(violation_samples) > balanced_count:
            violation_samples = random.sample(violation_samples, balanced_count)
        print(f"[BalancedSampling] 违规样本: {len(violation_samples)} 个")

        combined = pass_samples + violation_samples
        random.shuffle(combined)

        print(f"[BalancedSampling] 最终样本: 正常={len(pass_samples)}, 违规={len(violation_samples)}, 总计={len(combined)}")
        print(f"[BalancedSampling] ✓ 所有样本唯一，无重复")
        return combined

    def get_sample_count(self) -> int:
        with self._lock:
            return self._get_count()

    def get_sample_ids(self, limit: int) -> List[int]:
        if limit <= 0:
            return []
        ids: List[int] = []
        with self._lock:
            sid = self._next_id() - 1
            while sid > 0 and len(ids) < limit:
                if self.db.get(_sample_key(sid)) is not None:
                    ids.append(sid)
                sid -= 1
        return ids

    def load_by_ids(self, ids: List[int]) -> List[Sample]:
        if not ids:
            return []
        out: List[Sample] = []
        with self._lock:
            for sid in ids:
                sample = self._load_sample_by_id(int(sid))
                if sample is not None:
                    out.append(sample)
        return out

    def find_by_text(self, text: str) -> Optional[Sample]:
        text_hash = _hash_text(text)
        with self._lock:
            sid_raw = self.db.get(_text_latest_key(text_hash))
            if sid_raw is not None:
                if isinstance(sid_raw, bytes):
                    sid_raw = sid_raw.decode("utf-8")
                sample = self._load_sample_by_id(int(sid_raw))
                if sample is not None and sample.text == text:
                    return sample

            # Fallback scan for hash collision / stale pointer.
            sid = self._next_id() - 1
            while sid > 0:
                sample = self._load_sample_by_id(sid)
                if sample is not None and sample.text == text:
                    return sample
                sid -= 1
        return None

    def get_label_counts(self) -> Tuple[int, int]:
        with self._lock:
            return self._get_label_count(0), self._get_label_count(1)

    def cleanup_excess_samples(self, max_items: int):
        total = self.get_sample_count()
        if total <= max_items:
            print(f"[DB清理] 总样本数 {total} <= {max_items}，无需清理")
            return

        pass_count, violation_count = self.get_label_counts()
        print(f"[DB清理] 当前样本分布: 成功={pass_count}, 失败={violation_count}, 总计={total}")

        target_per_label = max_items // 2
        deleted_count = 0

        if pass_count > target_per_label:
            excess = pass_count - target_per_label
            print(f"[DB清理] 成功样本超出限制 ({pass_count} > {target_per_label})，需删除 {excess} 条")
            deleted = self._delete_random_samples(label=0, count=excess)
            deleted_count += deleted
            print(f"[DB清理] 已删除 {deleted} 条成功样本")

        if violation_count > target_per_label:
            excess = violation_count - target_per_label
            print(f"[DB清理] 失败样本超出限制 ({violation_count} > {target_per_label})，需删除 {excess} 条")
            deleted = self._delete_random_samples(label=1, count=excess)
            deleted_count += deleted
            print(f"[DB清理] 已删除 {deleted} 条失败样本")

        if deleted_count > 0:
            new_total = self.get_sample_count()
            new_pass, new_violation = self.get_label_counts()
            print(f"[DB清理] 清理完成: 删除 {deleted_count} 条，剩余 {new_total} 条")
            print(f"[DB清理] 新的样本分布: 成功={new_pass}, 失败={new_violation}")
        else:
            print(f"[DB清理] 无需删除样本")

    def _load_samples_by_label(self, label: int, limit: int) -> List[Sample]:
        if limit <= 0:
            return []
        out: List[Sample] = []
        with self._lock:
            sid = self._next_id() - 1
            while sid > 0 and len(out) < limit:
                sample = self._load_sample_by_id(sid)
                if sample is not None and sample.label == label:
                    out.append(sample)
                sid -= 1
        return out

    def _refresh_text_latest_after_delete(self, text_hash: str, deleted_id: int) -> None:
        latest = self.db.get(_text_latest_key(text_hash))
        if latest is None:
            return
        if isinstance(latest, bytes):
            latest = latest.decode("utf-8")
        if int(latest) != deleted_id:
            return

        sid = self._next_id() - 1
        while sid > 0:
            sample = self._load_sample_by_id(sid)
            if sample is not None:
                raw = self.db.get(_sample_key(sid))
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                if raw is not None and json_loads(raw).get("text_hash") == text_hash:
                    self.db[_text_latest_key(text_hash)] = str(sid)
                    return
            sid -= 1
        del self.db[_text_latest_key(text_hash)]

    def _delete_random_samples(self, label: int, count: int) -> int:
        if count <= 0:
            return 0

        with self._lock:
            candidates: List[int] = []
            sid = self._next_id() - 1
            while sid > 0:
                sample = self._load_sample_by_id(sid)
                if sample is not None and sample.label == label:
                    candidates.append(sid)
                sid -= 1

            if not candidates:
                return 0
            if len(candidates) <= count:
                to_delete = candidates
            else:
                to_delete = random.sample(candidates, count)

            count_0 = self._get_label_count(0)
            count_1 = self._get_label_count(1)
            total = self._get_count()

            for sid in to_delete:
                raw = self.db.get(_sample_key(sid))
                if raw is None:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                data = json_loads(raw)
                text_hash = data.get("text_hash", "")
                sample_label = int(data.get("label", 0))
                del self.db[_sample_key(sid)]
                if sample_label == 0:
                    count_0 = max(0, count_0 - 1)
                else:
                    count_1 = max(0, count_1 - 1)
                total = max(0, total - 1)
                if text_hash:
                    self._refresh_text_latest_after_delete(text_hash, sid)

            self._set_counts(total, count_0, count_1)
            return len(to_delete)
