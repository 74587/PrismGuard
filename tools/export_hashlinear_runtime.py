#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tools.hashlinear_light_runtime import RUNTIME_VERSION


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _atomic_replace_bytes(target: Path, data: bytes) -> None:
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


def _atomic_replace_text(target: Path, text: str) -> None:
    _atomic_replace_bytes(target, text.encode("utf-8"))


def export_model(input_path: Path, output_prefix: Path) -> tuple[Path, Path]:
    import joblib
    import numpy as np

    payload = joblib.load(input_path)
    clf = payload["clf"]
    cfg = payload["cfg"]

    coef = np.asarray(clf.coef_, dtype=np.float32)
    if coef.ndim != 2 or coef.shape[0] != 1:
        raise RuntimeError(f"Only binary SGDClassifier is supported, got coef shape={coef.shape}")

    intercept = np.asarray(clf.intercept_, dtype=np.float32)
    classes = [int(x) for x in clf.classes_.tolist()]
    if classes != [0, 1]:
        raise RuntimeError(f"Expected binary classes [0, 1], got {classes}")

    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    coef_path = output_prefix.with_suffix(".coef.f32")
    meta_path = output_prefix.with_suffix(".json")

    _atomic_replace_bytes(
        coef_path,
        coef[0].astype(np.float32, copy=False).tobytes(),
    )

    meta = {
        "runtime_version": RUNTIME_VERSION,
        "source_model": str(input_path),
        "n_features": int(coef.shape[1]),
        "intercept": float(intercept[0]),
        "classes": classes,
        "cfg": {
            "analyzer": cfg.get("analyzer", "char"),
            "ngram_range": list(cfg.get("ngram_range", [2, 4])),
            "n_features": int(cfg.get("n_features", coef.shape[1])),
            "alternate_sign": bool(cfg.get("alternate_sign", False)),
            "norm": cfg.get("norm", "l2"),
            "lowercase": True,
            "use_jieba": bool(cfg.get("use_jieba", False)),
        },
    }
    _atomic_replace_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    return meta_path, coef_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Path to hashlinear_model.pkl")
    parser.add_argument("output_prefix", help="Output prefix, e.g. /tmp/hashlinear_runtime")
    args = parser.parse_args()

    meta_path, coef_path = export_model(Path(args.input), Path(args.output_prefix))
    print(meta_path)
    print(coef_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
