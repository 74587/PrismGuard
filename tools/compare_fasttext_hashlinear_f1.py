#!/usr/bin/env python3
"""
对比 fastText vs HashLinear 的 F1（同一批样本、同一阈值）

用法:
  python tools/compare_fasttext_hashlinear_f1.py <profile_name> [--sample-size N] [--threshold T] [--seed S] [--sweep]

说明:
  - sample-size 为每个标签最多采样数；0 表示全量（可能很慢）
  - fastText 使用该 profile 的 fasttext_training 分词配置（如 jieba/tiktoken）
  - 两者均使用阈值 T（默认 0.5）将概率转为 0/1
  - --sweep 会在多个阈值上扫描并输出最佳 F1
"""

import argparse
import random
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from ai_proxy.moderation.smart.profile import get_profile
from ai_proxy.moderation.smart.storage import SampleStorage
from ai_proxy.moderation.smart.fasttext_model import fasttext_model_exists, _load_fasttext_with_cache
from ai_proxy.moderation.smart.fasttext_model_jieba import tokenize_text
from ai_proxy.moderation.smart.hashlinear_model import hashlinear_model_exists, hashlinear_predict_proba


def _f1(tp: int, fp: int, fn: int) -> float:
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    return 2 * (prec * rec) / (prec + rec) if (prec + rec) else 0.0


def _pr(tp: int, fp: int, fn: int) -> tuple[float, float]:
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    return prec, rec


def _accumulate_counts(y_true: list[int], y_pred: list[int]) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for t, p in zip(y_true, y_pred):
        if t == 1 and p == 1:
            tp += 1
        elif t == 0 and p == 1:
            fp += 1
        elif t == 1 and p == 0:
            fn += 1
    return tp, fp, fn


def _best_threshold(y_true: list[int], y_score: list[float]) -> tuple[float, float, int, int, int]:
    best_t = 0.5
    best_f1 = -1.0
    best_counts = (0, 0, 0)

    # coarse sweep first, then local refine
    candidates = [i / 100 for i in range(5, 96, 5)]
    candidates += [i / 100 for i in range(10, 91, 2)]

    seen = set()
    for t in candidates:
        if t in seen:
            continue
        seen.add(t)
        y_pred = [1 if s >= t else 0 for s in y_score]
        tp, fp, fn = _accumulate_counts(y_true, y_pred)
        f1 = _f1(tp, fp, fn)
        if f1 > best_f1:
            best_f1 = f1
            best_t = t
            best_counts = (tp, fp, fn)

    return best_t, best_f1, best_counts[0], best_counts[1], best_counts[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile_name")
    ap.add_argument("--sample-size", type=int, default=1000)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    profile = get_profile(args.profile_name)

    if not fasttext_model_exists(profile):
        print(f"❌ fastText 模型不存在: {profile.get_fasttext_model_path()}")
        print(f"   先训练: python tools/train_fasttext_model.py {args.profile_name}")
        sys.exit(1)
    if not hashlinear_model_exists(profile):
        print(f"❌ HashLinear 模型不存在: {profile.get_hashlinear_model_path()}")
        print(f"   先训练: python tools/train_hashlinear_model.py {args.profile_name}")
        sys.exit(1)

    storage = SampleStorage(profile.get_db_path())
    total = storage.get_sample_count()
    if total == 0:
        print("❌ 数据库中没有样本")
        sys.exit(1)

    pass_count, violation_count = storage.get_label_counts()

    rng = random.Random(args.seed)

    if args.sample_size > 0:
        take0 = min(args.sample_size, pass_count)
        take1 = min(args.sample_size, violation_count)
        pass_samples = storage._load_samples_by_label(0, take0)
        vio_samples = storage._load_samples_by_label(1, take1)
        rng.shuffle(pass_samples)
        rng.shuffle(vio_samples)
        pass_samples = pass_samples[:take0]
        vio_samples = vio_samples[:take1]
        samples = pass_samples + vio_samples
        rng.shuffle(samples)
    else:
        samples = storage.load_samples(max_samples=total)

    if not samples:
        print("❌ 没有可用样本")
        sys.exit(1)

    # 预加载 fastText 模型（避免循环中反复加载）
    ft_model = _load_fasttext_with_cache(profile)
    ft_cfg = profile.config.fasttext_training
    use_jieba = ft_cfg.use_jieba
    use_tiktoken = ft_cfg.use_tiktoken
    tiktoken_model = ft_cfg.tiktoken_model

    y_true: list[int] = []
    p_ft_list: list[float] = []
    p_hl_list: list[float] = []

    for s in samples:
        text = s.text.replace("\r", " ").replace("\n", " ")
        y_true.append(int(s.label))

        ft_text = text
        if use_jieba or use_tiktoken:
            ft_text = tokenize_text(ft_text, use_jieba, use_tiktoken, tiktoken_model)
        labels, probs = ft_model.predict(ft_text, k=2)
        p_ft = 0.0
        for lab, p in zip(labels, probs):
            if lab == "__label__1":
                p_ft = float(p)
                break
        p_ft_list.append(p_ft)

        p_hl_list.append(hashlinear_predict_proba(text, profile))

    if args.sweep:
        best_t_ft, best_f1_ft, tp_ft, fp_ft, fn_ft = _best_threshold(y_true, p_ft_list)
        best_t_hl, best_f1_hl, tp_hl, fp_hl, fn_hl = _best_threshold(y_true, p_hl_list)
        prec_ft, rec_ft = _pr(tp_ft, fp_ft, fn_ft)
        prec_hl, rec_hl = _pr(tp_hl, fp_hl, fn_hl)

        print("\n" + "=" * 80)
        print(f"Profile: {args.profile_name} | samples={len(y_true)} | seed={args.seed} | sweep=on")
        print("=" * 80)
        print(f"fastText   best_F1={best_f1_ft:.4f} @ t={best_t_ft:.2f} (P={prec_ft:.4f} R={rec_ft:.4f} tp={tp_ft} fp={fp_ft} fn={fn_ft})")
        print(f"hashlinear best_F1={best_f1_hl:.4f} @ t={best_t_hl:.2f} (P={prec_hl:.4f} R={rec_hl:.4f} tp={tp_hl} fp={fp_hl} fn={fn_hl})")
        return

    threshold = float(args.threshold)
    pred_ft = [1 if s >= threshold else 0 for s in p_ft_list]
    pred_hl = [1 if s >= threshold else 0 for s in p_hl_list]
    tp_ft, fp_ft, fn_ft = _accumulate_counts(y_true, pred_ft)
    tp_hl, fp_hl, fn_hl = _accumulate_counts(y_true, pred_hl)
    f1_ft = _f1(tp_ft, fp_ft, fn_ft)
    f1_hl = _f1(tp_hl, fp_hl, fn_hl)

    print("\n" + "=" * 70)
    print(f"Profile: {args.profile_name} | samples={len(y_true)} | threshold={threshold:.2f} | seed={args.seed}")
    print("=" * 70)
    print(f"fastText   F1: {f1_ft:.4f} (tp={tp_ft}, fp={fp_ft}, fn={fn_ft})")
    print(f"hashlinear F1: {f1_hl:.4f} (tp={tp_hl}, fp={fp_hl}, fn={fn_hl})")


if __name__ == "__main__":
    main()
