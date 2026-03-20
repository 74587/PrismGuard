#!/usr/bin/env python3
"""
环境依赖检查工具
"""
import os
import sys
import tempfile
from importlib import metadata


class DependencyError(Exception):
    """自定义依赖错误异常"""


def _parse_major_minor(version: str) -> tuple[int, int]:
    parts = version.split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    return major, minor


def _get_installed_version(package_name: str) -> str | None:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return None


def _deep_check_fasttext() -> None:
    try:
        import fasttext
    except ImportError:
        print("[ENV_CHECK] fastText 未安装，如果使用 fastText 模型可能会失败。")
        return

    fd, train_file = tempfile.mkstemp(suffix=".txt", prefix="fasttext_compat_check_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("__label__0 test\n")

        model = fasttext.train_supervised(input=train_file, dim=1, epoch=1, verbose=0)
        labels, _ = model.predict("test")
        if not labels:
            raise DependencyError("fastText 深度检查失败：预测返回空结果")
        print("[ENV_CHECK] fastText 与 NumPy 兼容性测试通过")
    except Exception as e:
        error_msg = str(e)
        if "copy" in error_msg and "array" in error_msg:
            raise DependencyError(
                "fastText 与 NumPy 不兼容! \n"
                "请降级 NumPy: pip install 'numpy<2.0'"
            ) from e
        print(f"[ENV_CHECK] fastText 深度检查出现警告: {e}")
    finally:
        if os.path.exists(train_file):
            os.remove(train_file)


def check_dependencies(deep_check: bool = False) -> None:
    """
    检查关键依赖项。

    默认使用轻量模式，只读取已安装包元数据，不导入 numpy/fasttext。
    deep_check=True 时才执行 fastText 的实际导入和功能测试。
    """
    mode = "deep" if deep_check else "light"
    print(f"[ENV_CHECK] 开始检查关键依赖项 (mode={mode})...")

    numpy_version = _get_installed_version("numpy")
    if not numpy_version:
        raise DependencyError("NumPy 未安装。请运行: pip install 'numpy<2.0'")

    try:
        major, _minor = _parse_major_minor(numpy_version)
    except Exception as e:
        raise DependencyError(f"无法解析 NumPy 版本 {numpy_version}: {e}") from e

    print(f"[ENV_CHECK] NumPy 版本: {numpy_version}")
    if major >= 2:
        raise DependencyError(
            f"NumPy 版本不兼容! 检测到版本 {numpy_version}，但 fastText 需要 < 2.0。\n"
            f"请降级 NumPy: pip install 'numpy<2.0'"
        )

    fasttext_version = _get_installed_version("fasttext")
    if fasttext_version:
        print(f"[ENV_CHECK] fastText 版本: {fasttext_version}")
    else:
        print("[ENV_CHECK] fastText 未安装，如果使用 fastText 模型可能会失败。")

    if deep_check and fasttext_version:
        _deep_check_fasttext()

    print("[ENV_CHECK] 依赖项检查通过。")


if __name__ == "__main__":
    try:
        deep = "--deep" in sys.argv
        check_dependencies(deep_check=deep)
        print("\n✅ 环境配置看起来没问题！")
    except DependencyError as e:
        print(f"\n❌ 环境检查失败: {e}", file=sys.stderr)
        sys.exit(1)
