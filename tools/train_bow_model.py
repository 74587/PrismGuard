#!/usr/bin/env python3
"""
词袋模型训练工具（可被主进程调度器以子进程方式调用）

重要约定（自动训练依赖）：
- 本脚本会被 [`ai_proxy/moderation/smart/scheduler._run_training_subprocess()`](ai_proxy/moderation/smart/scheduler.py:1)
  以 `sys.executable -u tools/train_bow_model.py <profile>` 启动，用于把训练峰值内存隔离在子进程中。
- 跨进程互斥通过 profile 目录下的 `.train.lock` 实现；若锁已存在表示已有训练在进行中。
- 当检测到锁已存在时，本脚本应以 exit code=2 退出（调度器据此“跳过本轮”，而非视为训练失败）。
  - exit code=0: 训练完成
  - exit code=1: 训练失败/异常
  - exit code=2: 锁占用/已有训练进行中
"""
import sys
import os
import time

sys.path.insert(0, ".")

from ai_proxy.moderation.smart.profile import get_profile, ModerationProfile
from ai_proxy.moderation.smart.bow import train_bow_model


def _training_lock_path(profile: ModerationProfile) -> str:
    return os.path.join(profile.base_dir, ".train.lock")


def _acquire_file_lock(lock_path: str, stale_seconds: int = 24 * 3600) -> bool:
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            payload = f"pid={os.getpid()}\ncreated_at={int(time.time())}\n"
            os.write(fd, payload.encode("utf-8", errors="replace"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        try:
            mtime = os.path.getmtime(lock_path)
            if (time.time() - mtime) > stale_seconds:
                os.remove(lock_path)
                return _acquire_file_lock(lock_path, stale_seconds=stale_seconds)
        except Exception:
            pass
        return False


def _release_file_lock(lock_path: str) -> None:
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return
    except Exception:
        return

def main():
    if len(sys.argv) < 2:
        print("用法: python tools/train_bow_model.py <profile_name>")
        print("示例: python tools/train_bow_model.py 4claude")
        sys.exit(1)
    
    profile_name = sys.argv[1]
    profile = get_profile(profile_name)
    
    print(f"开始训练 {profile_name} 的词袋模型...")
    print(f"数据库: {profile.get_db_path()}")
    print(f"模型输出: {profile.get_model_path()}")
    print(f"向量化器输出: {profile.get_vectorizer_path()}")
    print()
    
    lock_path = _training_lock_path(profile)
    if not _acquire_file_lock(lock_path):
        print(f"\n❌ 当前配置正在训练中（文件锁存在）: {lock_path}")
        sys.exit(2)

    try:
        train_bow_model(profile)
        print("\n✅ 训练完成！")
    except Exception as e:
        print(f"\n❌ 训练失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        _release_file_lock(lock_path)

if __name__ == "__main__":
    main()