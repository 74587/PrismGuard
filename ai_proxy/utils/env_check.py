#!/usr/bin/env python3
"""
环境依赖检查工具
"""
import sys
import os
import tempfile

class DependencyError(Exception):
    """自定义依赖错误异常"""
    pass

def check_dependencies():
    """
    检查所有关键依赖项的兼容性。
    如果发现问题，则抛出 DependencyError。
    """
    print("[ENV_CHECK] 开始检查关键依赖项...")
    
    # 检查 NumPy
    try:
        import numpy as np
        version = np.__version__
        major, minor = map(int, version.split('.')[:2])
        
        print(f"[ENV_CHECK] NumPy 版本: {version}")
        
        if major >= 2:
            raise DependencyError(
                f"NumPy 版本不兼容! 检测到版本 {version}，但 fastText 需要 < 2.0。\n"
                f"请降级 NumPy: pip install 'numpy<2.0'"
            )
    except ImportError:
        raise DependencyError("NumPy 未安装。请运行: pip install 'numpy<2.0'")
    except DependencyError:
        raise
    except Exception as e:
        raise DependencyError(f"检查 NumPy 时发生未知错误: {e}")

    # 检查 fastText
    try:
        import fasttext
        print(f"[ENV_CHECK] fastText 已安装")
        
        # 尝试一个简单的 fastText 操作来最终确认兼容性
        fd, train_file = tempfile.mkstemp(suffix=".txt", prefix="fasttext_compat_check_")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write("__label__0 test\n")
            
            model = fasttext.train_supervised(input=train_file, dim=1, epoch=1, verbose=0)
            model.predict("test")
            print("[ENV_CHECK] fastText 与 NumPy 兼容性测试通过")
        finally:
            if os.path.exists(train_file):
                os.remove(train_file)

    except ImportError:
        # fastText 不是必需的，如果 BoW 是默认模型，可以跳过
        print("[ENV_CHECK] fastText 未安装，如果使用 fastText 模型可能会失败。")
    except Exception as e:
        error_msg = str(e)
        if "copy" in error_msg and "array" in error_msg:
             raise DependencyError(
                f"fastText 与 NumPy 不兼容! \n"
                f"请降级 NumPy: pip install 'numpy<2.0'"
            )
        else:
            print(f"[ENV_CHECK] fastText 功能测试时出现警告: {e}")

    print("[ENV_CHECK] 依赖项检查通过。")

if __name__ == "__main__":
    try:
        check_dependencies()
        print("\n✅ 环境配置看起来没问题！")
    except DependencyError as e:
        print(f"\n❌ 环境检查失败: {e}", file=sys.stderr)
        sys.exit(1)