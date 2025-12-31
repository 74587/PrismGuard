#!/usr/bin/env python3
"""
词袋模型训练工具（可被主进程调度器以子进程方式调用）

重要约定（自动训练依赖）：
- 本脚本会被 [`ai_proxy/moderation/smart/scheduler._run_training_subprocess()`](ai_proxy/moderation/smart/scheduler.py:1)
  以 `sys.executable -u tools/train_bow_model.py <profile>` 启动，用于把训练峰值内存隔离在子进程中。
- 跨进程互斥通过 profile 目录下的 `.train.lock` 实现；若锁已存在表示已有训练在进行中。
- 当检测到锁已存在时，本脚本应以 exit code=2 退出（调度器据此"跳过本轮"，而非视为训练失败）。
  - exit code=0: 训练完成
  - exit code=1: 训练失败/异常
  - exit code=2: 锁占用/已有训练进行中

改进：
- 锁超时从 24 小时改为 2 小时
- 检查锁持有进程是否存活
- 添加训练状态记录
- 训练后验证模型文件
"""
import sys
import os
import time
import json
import joblib

sys.path.insert(0, ".")

from ai_proxy.moderation.smart.profile import get_profile, ModerationProfile
from ai_proxy.moderation.smart.bow import train_bow_model


def _training_lock_path(profile: ModerationProfile) -> str:
    return os.path.join(profile.base_dir, ".train.lock")


def _training_status_path(profile: ModerationProfile) -> str:
    """训练状态文件路径"""
    return os.path.join(profile.base_dir, ".train_status.json")


def _parse_lock_file(lock_path: str) -> dict:
    """解析锁文件内容，返回 {pid, created_at}"""
    try:
        with open(lock_path, 'r', encoding='utf-8') as f:
            content = f.read()
        result = {}
        for line in content.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                result[key.strip()] = value.strip()
        return result
    except Exception:
        return {}


def _is_process_alive(pid: int) -> bool:
    """检查进程是否存活"""
    try:
        if sys.platform == 'win32':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        return True  # 无法确定时假设存活


def _acquire_file_lock(lock_path: str, stale_seconds: int = 2 * 3600) -> bool:
    """
    获取文件锁
    
    改进：
    1. 默认超时从 24 小时改为 2 小时
    2. 检查锁持有进程是否存活
    3. 如果锁是调度器创建的，子进程可以继承使用
    """
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            payload = f"pid={os.getpid()}\ncreated_at={int(time.time())}\nhostname={os.environ.get('COMPUTERNAME', os.environ.get('HOSTNAME', 'unknown'))}\ntype=subprocess\n"
            os.write(fd, payload.encode("utf-8", errors="replace"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        try:
            lock_info = _parse_lock_file(lock_path)
            lock_pid = int(lock_info.get('pid', 0))
            lock_created = int(lock_info.get('created_at', 0))
            lock_type = lock_info.get('type', '')
            
            # 如果锁是调度器创建的，检查调度器是否是我们的父进程
            if lock_type == 'scheduler':
                parent_pid = os.getppid()
                if lock_pid == parent_pid:
                    print(f"[LOCK] 继承调度器的锁 (父进程 PID={parent_pid})")
                    try:
                        with open(lock_path, 'w', encoding='utf-8') as f:
                            f.write(f"pid={os.getpid()}\ncreated_at={int(time.time())}\nhostname={os.environ.get('COMPUTERNAME', os.environ.get('HOSTNAME', 'unknown'))}\ntype=subprocess\nparent_pid={parent_pid}\n")
                        return True
                    except Exception:
                        pass
            
            if lock_created > 0 and (time.time() - lock_created) > stale_seconds:
                print(f"[LOCK] 锁已过期 ({(time.time() - lock_created) / 3600:.1f} 小时)，清理中...")
                os.remove(lock_path)
                return _acquire_file_lock(lock_path, stale_seconds=stale_seconds)
            
            if lock_pid > 0 and not _is_process_alive(lock_pid):
                print(f"[LOCK] 锁持有进程 (PID={lock_pid}) 已不存在，清理中...")
                os.remove(lock_path)
                return _acquire_file_lock(lock_path, stale_seconds=stale_seconds)
            
            if lock_pid > 0:
                print(f"[LOCK] 锁被进程 PID={lock_pid} 持有，创建于 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(lock_created))}")
            
        except Exception as e:
            print(f"[LOCK] 检查锁状态时出错: {e}")
        return False


def _release_file_lock(lock_path: str) -> None:
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        return
    except Exception:
        return


def _save_training_status(profile: ModerationProfile, status: str, error: str = None):
    """保存训练状态到文件"""
    status_path = _training_status_path(profile)
    status_data = {
        'status': status,
        'timestamp': int(time.time()),
        'timestamp_str': time.strftime('%Y-%m-%d %H:%M:%S'),
        'pid': os.getpid(),
        'model_path': profile.get_model_path(),
    }
    if error:
        status_data['error'] = str(error)[:500]
    
    try:
        with open(status_path, 'w', encoding='utf-8') as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARNING] 无法保存训练状态: {e}")


def _validate_model_file(profile: ModerationProfile) -> bool:
    """验证模型文件是否有效"""
    model_path = profile.get_model_path()
    vectorizer_path = profile.get_vectorizer_path()
    
    # 检查文件存在
    if not os.path.exists(model_path):
        print(f"[VALIDATE] 模型文件不存在: {model_path}")
        return False
    if not os.path.exists(vectorizer_path):
        print(f"[VALIDATE] 向量化器文件不存在: {vectorizer_path}")
        return False
    
    # 检查文件大小
    model_size = os.path.getsize(model_path)
    vectorizer_size = os.path.getsize(vectorizer_path)
    if model_size < 100:
        print(f"[VALIDATE] 模型文件过小 ({model_size} bytes)")
        return False
    if vectorizer_size < 100:
        print(f"[VALIDATE] 向量化器文件过小 ({vectorizer_size} bytes)")
        return False
    
    # 尝试加载和预测
    try:
        vectorizer = joblib.load(vectorizer_path)
        clf = joblib.load(model_path)
        test_vec = vectorizer.transform(["测试文本"])
        _ = clf.predict_proba(test_vec)
    except Exception as e:
        print(f"[VALIDATE] 模型验证失败: {e}")
        return False
    
    print(f"[VALIDATE] 模型验证通过")
    return True


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

    # 记录训练开始状态
    _save_training_status(profile, 'started')

    try:
        train_bow_model(profile)
        
        # 验证模型文件是否有效
        if not _validate_model_file(profile):
            raise RuntimeError("训练后模型文件验证失败")
        
        # 记录训练完成状态
        _save_training_status(profile, 'completed')
        
        print("\n✅ 训练完成！")
    except Exception as e:
        # 记录训练失败状态
        _save_training_status(profile, 'failed', error=str(e))
        print(f"\n❌ 训练失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        _release_file_lock(lock_path)


if __name__ == "__main__":
    main()
