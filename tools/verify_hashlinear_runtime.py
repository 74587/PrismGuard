#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_proxy.moderation.smart.profile import ModerationProfile
from ai_proxy.moderation.smart.storage import SampleStorage
from tools.hashlinear_light_runtime import HashlinearRuntime


def collect_texts(profile: ModerationProfile, limit: int) -> list[str]:
    storage = SampleStorage(profile.get_db_path(), read_only=True)
    samples = storage.load_samples(max_samples=limit)
    texts = [s.text for s in samples if s.text]
    if not texts:
        texts = [
            "hello world",
            "违规测试 forbidden words sample",
            "normal chat request",
            "一些中文文本 mixed English 123",
        ]
    return texts


def verify(profile_name: str, runtime_prefix: Path, limit: int) -> dict:
    profile = ModerationProfile(profile_name)
    model_path = Path(profile.get_hashlinear_model_path())
    payload = joblib.load(model_path)
    clf = payload["clf"]
    cfg = payload["cfg"]

    from ai_proxy.moderation.smart.hashlinear_model import (
        _maybe_tokenize_for_word_analyzer,
        _vectorizer_from_cfg,
    )

    vec = _vectorizer_from_cfg(cfg)
    runtime = HashlinearRuntime.load(runtime_prefix)
    texts = collect_texts(profile, limit)

    comparisons = []
    max_abs_diff = 0.0
    total_abs_diff = 0.0

    for text in texts:
        clean = text.replace("\r", " ").replace("\n", " ")
        clean = _maybe_tokenize_for_word_analyzer(clean, cfg)
        skl = float(clf.predict_proba(vec.transform([clean]))[0, 1])
        lite = float(runtime.predict_proba(clean))
        diff = abs(skl - lite)
        total_abs_diff += diff
        max_abs_diff = max(max_abs_diff, diff)
        comparisons.append(
            {
                "text_preview": text[:120],
                "sklearn": skl,
                "light_runtime": lite,
                "abs_diff": diff,
            }
        )

    comparisons.sort(key=lambda x: x["abs_diff"], reverse=True)
    return {
        "profile": profile_name,
        "model_path": str(model_path),
        "runtime_prefix": str(runtime_prefix),
        "num_texts": len(texts),
        "max_abs_diff": max_abs_diff,
        "mean_abs_diff": (total_abs_diff / len(texts)) if texts else 0.0,
        "worst_examples": comparisons[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", help="Profile name, e.g. 4claudecode")
    parser.add_argument("runtime_prefix", help="Runtime file prefix")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    report = verify(args.profile, Path(args.runtime_prefix), args.limit)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(args.output)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
