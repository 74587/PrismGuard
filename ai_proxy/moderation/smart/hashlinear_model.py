"""
Hashing + 线性分类（SGD Logistic）本地模型

目标：
- 模型文件 < 50MB（通常远小于 10MB）
- 推理 CPU/内存占用极低
- 性能对标 fastText（尤其在字符扰动/中英混写场景）
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import joblib
import numpy as np

from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.linear_model import SGDClassifier

from ai_proxy.moderation.smart.profile import ModerationProfile, SampleLoadingStrategy
from ai_proxy.moderation.smart.storage import SampleStorage
from ai_proxy.utils.memory_guard import release_memory


@dataclass(frozen=True)
class _HashLinearBundle:
    clf: SGDClassifier
    cfg: dict
    mtime: float


_model_cache: Dict[str, _HashLinearBundle] = {}


def _vectorizer_from_cfg(cfg: dict) -> HashingVectorizer:
    analyzer = cfg.get("analyzer", "char")
    ngram_range = tuple(cfg.get("ngram_range", [2, 4]))
    n_features = int(cfg.get("n_features", 1_048_576))
    alternate_sign = bool(cfg.get("alternate_sign", False))
    norm = cfg.get("norm", "l2")

    return HashingVectorizer(
        analyzer=analyzer,
        ngram_range=ngram_range,
        n_features=n_features,
        alternate_sign=alternate_sign,
        norm=norm,
        lowercase=True,
    )


def _model_path(profile: ModerationProfile) -> str:
    return profile.get_hashlinear_model_path()


def hashlinear_model_exists(profile: ModerationProfile) -> bool:
    return os.path.exists(_model_path(profile))


def _remove_corrupted_model(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"[INFO] 已删除损坏的 HashLinear 模型文件: {path}")
    except Exception as e:
        print(f"[WARNING] 无法删除损坏的 HashLinear 模型文件: {path}, 错误: {e}")


def _load_hashlinear_with_cache(profile: ModerationProfile) -> Tuple[SGDClassifier, dict]:
    profile_name = profile.profile_name
    path = _model_path(profile)

    if not os.path.exists(path):
        raise FileNotFoundError(f"HashLinear 模型不存在: {path}")

    size = os.path.getsize(path)
    if size < 512:
        _remove_corrupted_model(path)
        raise RuntimeError(f"HashLinear 模型文件过小或损坏 ({size} bytes): {path}")

    mtime = os.path.getmtime(path)
    cached = _model_cache.get(profile_name)
    if cached is not None and cached.mtime == mtime:
        return cached.clf, cached.cfg

    if cached is not None:
        _model_cache.pop(profile_name, None)
        release_memory()

    try:
        payload = joblib.load(path)
        clf = payload["clf"]
        cfg = payload["cfg"]
    except Exception as e:
        _remove_corrupted_model(path)
        raise RuntimeError(f"HashLinear 模型加载失败: {path}, 错误: {e}") from e

    # 验证可用性
    try:
        vec = _vectorizer_from_cfg(cfg)
        X = vec.transform(["验证测试"])
        _ = clf.predict_proba(X)
    except Exception as e:
        _remove_corrupted_model(path)
        raise RuntimeError(f"HashLinear 模型验证失败: {path}, 错误: {e}") from e

    _model_cache[profile_name] = _HashLinearBundle(clf=clf, cfg=cfg, mtime=mtime)
    return clf, cfg


def _maybe_tokenize_for_word_analyzer(text: str, cfg: dict) -> str:
    # 最小化接入：默认不分词；仅当 analyzer=word 且 use_jieba=True 时启用
    if cfg.get("analyzer") != "word" or not cfg.get("use_jieba", False):
        return text
    try:
        import jieba

        return " ".join(jieba.cut(text))
    except Exception:
        return text


def train_hashlinear_model(profile: ModerationProfile) -> None:
    """
    训练 HashLinear 模型（HashingVectorizer + SGDClassifier(log_loss)）。
    """
    try:
        original_nice = os.nice(0)
        os.nice(19)
        print(f"[HashLinear] 训练进程优先级已调整 (nice: {original_nice} -> {os.nice(0)})")
    except Exception as e:
        print(f"[HashLinear] 无法调整进程优先级: {e}")

    storage = SampleStorage(profile.get_db_path())
    cfg_obj = profile.config.hashlinear_training

    storage.cleanup_excess_samples(cfg_obj.max_db_items)

    sample_count = storage.get_sample_count()
    if sample_count < cfg_obj.min_samples:
        print(f"[HashLinear] 样本数不足 {cfg_obj.min_samples}，当前={sample_count}，跳过训练")
        return

    if cfg_obj.sample_loading == SampleLoadingStrategy.latest_full:
        samples = storage.load_balanced_latest_samples(cfg_obj.max_samples)
        print(f"[HashLinear] 样本加载策略: latest_full (balanced latest)")
    elif cfg_obj.sample_loading == SampleLoadingStrategy.random_full:
        samples = storage.load_balanced_random_samples(cfg_obj.max_samples)
        print(f"[HashLinear] 样本加载策略: random_full (balanced random)")
    else:
        samples = storage.load_balanced_samples(cfg_obj.max_samples)
        print(f"[HashLinear] 样本加载策略: balanced_undersample")

    if not samples:
        print(f"[HashLinear] 无样本可用于训练，跳过")
        return

    cfg = cfg_obj.model_dump()

    print(f"[HashLinear] 开始训练，共 {len(samples)} 个样本")
    print(f"[HashLinear] 特征: analyzer={cfg['analyzer']} ngram_range={cfg['ngram_range']} n_features={cfg['n_features']} norm={cfg['norm']}")
    print(f"[HashLinear] 训练: epochs={cfg['epochs']} batch_size={cfg['batch_size']} alpha={cfg['alpha']}")

    vectorizer = _vectorizer_from_cfg(cfg)

    clf = SGDClassifier(
        loss="log_loss",
        alpha=float(cfg.get("alpha", 1e-5)),
        random_state=int(cfg.get("random_seed", 42)),
        fit_intercept=True,
        learning_rate="optimal",
    )

    rng = np.random.default_rng(int(cfg.get("random_seed", 42)))
    start = time.time()
    classes = np.array([0, 1], dtype=np.int64)

    texts = [s.text.replace("\r", " ").replace("\n", " ") for s in samples]
    labels = np.array([int(s.label) for s in samples], dtype=np.int64)

    epochs = int(cfg.get("epochs", 3))
    batch_size = int(cfg.get("batch_size", 2048))
    max_seconds = int(cfg.get("max_seconds", 300))

    first_fit = True
    last_print = start
    for ep in range(epochs):
        order = rng.permutation(len(texts))
        print(f"[HashLinear] Epoch {ep+1}/{epochs}...")
        for i in range(0, len(order), batch_size):
            elapsed = time.time() - start
            if elapsed > max_seconds:
                print(f"[HashLinear] 达到最大训练时间 {max_seconds}s，提前结束 (epoch={ep+1})")
                ep = epochs  # break outer after this loop
                break

            idx = order[i : i + batch_size]
            batch_texts = [_maybe_tokenize_for_word_analyzer(texts[j], cfg) for j in idx]
            y = labels[idx]
            X = vectorizer.transform(batch_texts)
            if first_fit:
                clf.partial_fit(X, y, classes=classes)
                first_fit = False
            else:
                clf.partial_fit(X, y)

            now = time.time()
            if (now - last_print) >= 5:
                done = min(i + batch_size, len(order))
                rate = done / max(now - start, 1e-9)
                eta = (len(order) - done) / max(rate, 1e-9)
                print(f"[HashLinear]  progress: {done}/{len(order)} | {rate:.1f} samples/s | ETA {eta:.1f}s | elapsed {elapsed:.1f}s")
                last_print = now

        if time.time() - start > max_seconds:
            break

    # 尽量缩小模型占用（存 float32 权重）
    try:
        if hasattr(clf, "coef_") and clf.coef_ is not None:
            clf.coef_ = clf.coef_.astype(np.float32, copy=False)
        if hasattr(clf, "intercept_") and clf.intercept_ is not None:
            clf.intercept_ = clf.intercept_.astype(np.float32, copy=False)
    except Exception:
        pass

    # 保存（临时文件 + 原子替换）
    model_path = _model_path(profile)
    temp_path = model_path + ".tmp"
    payload = {"clf": clf, "cfg": cfg, "trained_at": int(time.time())}

    joblib.dump(payload, temp_path, compress=3)

    if not os.path.exists(temp_path) or os.path.getsize(temp_path) < 512:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        raise RuntimeError("HashLinear 模型保存失败：临时文件无效")

    os.replace(temp_path, model_path)
    print(f"[HashLinear] 模型已保存: {model_path} ({os.path.getsize(model_path)/1024/1024:.2f} MB)")


def hashlinear_predict_proba(text: str, profile: ModerationProfile) -> float:
    """
    预测违规概率 p(violation)。
    """
    clf, cfg = _load_hashlinear_with_cache(profile)

    clean = text.replace("\r", " ").replace("\n", " ")
    clean = _maybe_tokenize_for_word_analyzer(clean, cfg)
    vec = _vectorizer_from_cfg(cfg)
    X = vec.transform([clean])

    proba = clf.predict_proba(X)
    return float(proba[0, 1])
