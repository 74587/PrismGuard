#!/usr/bin/env python3
"""
HashLinear 模型训练工具（HashingVectorizer + SGD Logistic）
用法: python tools/train_hashlinear_model.py <profile_name>

Exit codes:
  0: 训练完成
  1: 训练失败/异常
  2: 锁占用/已有训练进行中

注意：全局只允许同时运行一个训练任务（跨所有 profile 和模型类型）
"""

import json
import os
import sys
import time

try:
    import fcntl  # POSIX
except ImportError:
    fcntl = None
    import msvcrt  # Windows

sys.path.insert(0, ".")

from ai_proxy.moderation.smart.profile import get_profile, ModerationProfile
from ai_proxy.moderation.smart.hashlinear_model import train_hashlinear_model


GLOBAL_LOCK_PATH = "configs/mod_profiles/.global_train.lock"


def _acquire_global_lock(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    # Windows: msvcrt.locking requires locking a non-empty region.
    lock_file.seek(0)
    lock_file.write("0")
    lock_file.flush()
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)


def _release_global_lock(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


def _status_path(profile: ModerationProfile) -> str:
    return os.path.join(profile.base_dir, ".train_status.json")


def _log_path(profile: ModerationProfile) -> str:
    return os.path.join(profile.base_dir, "train.log")


class TeeWriter:
    def __init__(self, *writers):
        self.writers = writers

    def write(self, text):
        for w in self.writers:
            try:
                w.write(text)
                w.flush()
            except Exception:
                pass

    def flush(self):
        for w in self.writers:
            try:
                w.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def fileno(self):
        return self.writers[0].fileno()


def _save_status(profile: ModerationProfile, status: str, error: str | None = None):
    data = {"status": status, "timestamp": int(time.time()), "pid": os.getpid(), "model_type": "hashlinear"}
    if error:
        data["error"] = str(error)[:500]
    try:
        with open(_status_path(profile), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python tools/train_hashlinear_model.py <profile_name>")
        sys.exit(1)

    profile_name = sys.argv[1]
    profile = get_profile(profile_name)

    lock_file = None
    os.makedirs(os.path.dirname(GLOBAL_LOCK_PATH), exist_ok=True)

    try:
        lock_file = open(GLOBAL_LOCK_PATH, "w", encoding="utf-8")
        _acquire_global_lock(lock_file)
        lock_file.write(f"pid={os.getpid()}\nprofile={profile_name}\nmodel=hashlinear\ntime={int(time.time())}\n")
        lock_file.flush()
    except (IOError, OSError):
        print("[LOCK] 已有训练任务在进行中（全局锁），退出")
        if lock_file:
            lock_file.close()
        sys.exit(2)

    log_path = _log_path(profile)
    log_file = open(log_path, "w", encoding="utf-8")

    tee_out = TeeWriter(sys.stdout, log_file)
    tee_err = TeeWriter(sys.stderr, log_file)
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    try:
        sys.stdout = tee_out
        sys.stderr = tee_err

        from datetime import datetime

        start_time = datetime.now()
        print("=" * 50)
        print(f"HashLinear 训练: {profile_name}")
        print(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 50)

        _save_status(profile, "started")
        train_hashlinear_model(profile)

        model_path = profile.get_hashlinear_model_path()
        if not os.path.exists(model_path) or os.path.getsize(model_path) < 512:
            raise RuntimeError("模型验证失败")

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        _save_status(profile, "completed")

        print("\n" + "=" * 50)
        print("✅ 训练完成")
        print(f"模型: {model_path}")
        print(f"结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"耗时: {duration:.1f} 秒")
        print("=" * 50)

    except Exception as e:
        _save_status(profile, "failed", str(e))
        print(f"\n❌ 训练失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr

        log_file.close()

        if lock_file:
            try:
                _release_global_lock(lock_file)
            finally:
                lock_file.close()


if __name__ == "__main__":
    main()
