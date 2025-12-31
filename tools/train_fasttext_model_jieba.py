#!/usr/bin/env python3
"""
fastText 模型训练工具（jieba 分词版本，可被主进程调度器以子进程方式调用）

使用 jieba 分词 + fastText 训练，更适合中文文本

重要约定（自动训练依赖）：
- 本脚本可能被 [`ai_proxy/moderation/smart/scheduler._run_training_subprocess()`](ai_proxy/moderation/smart/scheduler.py:1)
  间接调用（通常调度器优先调用 `tools/train_fasttext_model.py`，由其根据配置选择分词方式）。
- 跨进程互斥通过 profile 目录下的 `.train.lock` 实现；若锁已存在表示已有训练在进行中。
- 当检测到锁已存在时，本脚本应以 exit code=2 退出（调度器据此"跳过本轮"，而非视为训练失败）。
  - exit code=0: 训练完成
  - exit code=1: 训练失败/异常
  - exit code=2: 锁占用/已有训练进行中

用法: python tools/train_fasttext_model_jieba.py <profile_name>

注意：推荐使用 tools/train_fasttext_model.py，它会根据配置自动选择分词方式。
"""
import sys
import os
import time
import json

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_proxy.moderation.smart.profile import ModerationProfile
from ai_proxy.moderation.smart.fasttext_model_jieba import train_fasttext_model_jieba
from ai_proxy.moderation.smart.storage import SampleStorage


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
            handle = kernel32.OpenProcess(0x1000, False, pid)
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
        return True


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
        'model_path': profile.get_fasttext_model_path(),
    }
    if error:
        status_data['error'] = str(error)[:500]
    
    try:
        with open(status_path, 'w', encoding='utf-8') as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARNING] 无法保存训练状态: {e}")


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/train_fasttext_model_jieba.py <profile_name>")
        print("示例: python tools/train_fasttext_model_jieba.py default")
        print("\n注意：推荐使用 tools/train_fasttext_model.py，它会根据配置自动选择分词方式。")
        sys.exit(1)
    
    profile_name = sys.argv[1]
    
    print(f"{'='*60}")
    print(f"fastText 模型训练工具（jieba 分词版本）")
    print(f"配置: {profile_name}")
    print(f"{'='*60}\n")
    
    # 加载配置
    profile = ModerationProfile(profile_name)
    
    # 显示配置信息
    cfg = profile.config.fasttext_training
    print(f"训练配置:")
    print(f"  最小样本数: {cfg.min_samples}")
    print(f"  最大样本数: {cfg.max_samples}")
    print(f"  维度: {cfg.dim}")
    print(f"  学习率: {cfg.lr}")
    print(f"  训练轮数: {cfg.epoch}")
    print(f"  词级 n-gram: {cfg.word_ngrams}")
    print(f"  分词方式: jieba（关闭子词 n-gram）")
    print()
    
    # 检查样本数据
    storage = SampleStorage(profile.get_db_path())
    sample_count = storage.get_sample_count()
    pass_count, violation_count = storage.get_label_counts()
    
    print(f"样本统计:")
    print(f"  总数: {sample_count}")
    print(f"  通过: {pass_count}")
    print(f"  违规: {violation_count}")
    
    if violation_count > 0:
        ratio = pass_count / violation_count
        print(f"  不平衡比例: {ratio:.2f}:1")
        if ratio > 10:
            print(f"  ⚠️  类别严重不平衡！建议增加违规样本")
    print()
    
    if sample_count < cfg.min_samples:
        print(f"❌ 样本数不足 {cfg.min_samples}，无法训练")
        sys.exit(1)
    
    # 开始训练
    print(f"开始训练（使用 jieba 分词）...\n")

    lock_path = _training_lock_path(profile)
    if not _acquire_file_lock(lock_path):
        print(f"\n❌ 当前配置正在训练中（文件锁存在）: {lock_path}")
        sys.exit(2)

    _save_training_status(profile, 'started')

    try:
        train_fasttext_model_jieba(profile)
        _save_training_status(profile, 'completed')
        print(f"\n✅ 训练完成")
        print(f"模型已保存: {profile.get_fasttext_model_path()}")
        print(f"\n💡 提示:")
        print(f"  - 使用 jieba 分词，更符合中文语言特性")
        print(f"  - 关闭了子词 n-gram，使用词级特征")
    except Exception as e:
        _save_training_status(profile, 'failed', error=str(e))
        print(f"\n❌ 训练失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        _release_file_lock(lock_path)


if __name__ == "__main__":
    main()
