#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUST_DIR = ROOT / "rust" / "hashlinear_rust_ext"


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    print(f"[build_hashlinear_rust] run: {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, text=True, check=False)


def _find_maturin() -> list[str] | None:
    python_cmd = [sys.executable, "-m", "maturin", "--version"]
    probe = subprocess.run(python_cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    if probe.returncode == 0:
        return [sys.executable, "-m", "maturin"]

    maturin = shutil.which("maturin")
    if maturin:
        return [maturin]
    return None


def main() -> int:
    print(f"[build_hashlinear_rust] python: {sys.executable}")
    print(f"[build_hashlinear_rust] project: {ROOT}")

    cargo = shutil.which("cargo")
    rustc = shutil.which("rustc")
    if not cargo or not rustc:
        print("[build_hashlinear_rust] error: cargo/rustc 未找到，跳过构建。")
        print("[build_hashlinear_rust] 需要本机已有 Rust 工具链，服务本身仍可继续走 Python fallback。")
        return 2

    maturin_cmd = _find_maturin()
    if maturin_cmd is None:
        print("[build_hashlinear_rust] error: maturin 未找到。")
        print("[build_hashlinear_rust] 请在当前 Python 环境中准备 maturin 后重试。")
        return 3

    result = _run(
        [
            *maturin_cmd,
            "develop",
            "--release",
            "--manifest-path",
            str(RUST_DIR / "Cargo.toml"),
        ],
        cwd=ROOT,
    )
    if result.returncode != 0:
        print(f"[build_hashlinear_rust] build failed with exit code {result.returncode}")
        return result.returncode

    verify = subprocess.run(
        [sys.executable, "-c", "import hashlinear_rust_ext; print(hashlinear_rust_ext.__name__)"],
        cwd=ROOT,
        text=True,
        check=False,
    )
    if verify.returncode != 0:
        print("[build_hashlinear_rust] error: 构建完成，但 Python 导入验证失败。")
        return 4

    print("[build_hashlinear_rust] success: hashlinear_rust_ext 已可导入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
