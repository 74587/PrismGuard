#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_proxy.moderation.smart.profile import ModerationProfile
from tools.export_hashlinear_runtime import export_model


def main() -> int:
    profiles_root = ROOT / "configs" / "mod_profiles"
    exported = 0
    skipped = 0
    failed = 0

    for profile_dir in sorted(p for p in profiles_root.iterdir() if p.is_dir()):
        profile = ModerationProfile(profile_dir.name)
        model_path = Path(profile.get_hashlinear_model_path())
        runtime_prefix = Path(profile.base_dir) / "hashlinear_runtime"

        if not model_path.exists():
            print(f"[SKIP] {profile.profile_name}: missing {model_path}")
            skipped += 1
            continue

        try:
            meta_path, coef_path = export_model(model_path, runtime_prefix)
            print(f"[OK] {profile.profile_name}: {meta_path.name}, {coef_path.name}")
            exported += 1
        except Exception as e:
            print(f"[FAIL] {profile.profile_name}: {e}")
            failed += 1

    print(f"exported={exported} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
