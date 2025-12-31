#!/usr/bin/env python3
"""
fastText 模型训练工具（可被主进程调度器以子进程方式调用）
用法: python tools/train_fasttext_model.py <profile_name>

重要约定（自动训练依赖）：
- 本脚本会被 [`ai_proxy/moderation/smart/scheduler._run_training_subprocess()`](ai_proxy/moderation/smart/scheduler.py:1)
  以 `sys.executable -u tools/train_fasttext_model.py <profile>` 启动，用于把训练峰值内存隔离在子进程中。
- 跨进程互斥通过 profile 目录下的 `.train.lock` 实现；若锁已存在表示已有训练在进行中。
- 当检测到锁已存在时，本脚本应以 exit code=2 退出（调度器据此“跳过本轮”，而非视为训练失败）。
  - exit code=0: 训练完成
  - exit code=1: 训练失败/异常
  - exit code=2: 锁占用/已有训练进行中

根据配置自动选择分词方式：
- use_tiktoken=false, use_jieba=false: 字符级 n-gram（原版）
- use_tiktoken=false, use_jieba=true: jieba 中文分词
- use_tiktoken=true, use_jieba=false: tiktoken BPE 分词
- use_tiktoken=true, use_jieba=true: tiktoken + jieba 组合（实验性）
"""
import sys
import os
import time

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_proxy.moderation.smart.profile import ModerationProfile
from ai_proxy.moderation.smart.fasttext_model import train_fasttext_model
from ai_proxy.moderation.smart.fasttext_model_jieba import train_fasttext_model_jieba
from ai_proxy.moderation.smart.storage import SampleStorage


def _validate_model_file(model_path: str) -> bool:
    """
    验证模型文件是否有效
    
    检查：
    1. 文件存在且大小合理
    2. 能够成功加载
    3. 能够进行基本预测
    
    Returns:
        True 如果模型有效，False 否则
    """
    import fasttext
    
    # 检查文件存在
    if not os.path.exists(model_path):
        print(f"[VALIDATE] 模型文件不存在: {model_path}")
        return False
    
    # 检查文件大小（至少 1KB，避免空文件或损坏文件）
    file_size = os.path.getsize(model_path)
    if file_size < 1024:
        print(f"[VALIDATE] 模型文件过小 ({file_size} bytes): {model_path}")
        return False
    
    # 尝试加载模型
    try:
        model = fasttext.load_model(model_path)
    except Exception as e:
        print(f"[VALIDATE] 模型加载失败: {e}")
        return False
    
    # 尝试进行预测
    try:
        labels, probs = model.predict("测试文本 test text", k=2)
        if not labels or len(labels) == 0:
            print(f"[VALIDATE] 模型预测返回空结果")
            return False
        
        # 检查标签格式
        valid_labels = {'__label__0', '__label__1'}
        for label in labels:
            if label not in valid_labels:
                print(f"[VALIDATE] 模型返回未知标签: {label}")
                return False
                
    except Exception as e:
        print(f"[VALIDATE] 模型预测失败: {e}")
        return False
    
    print(f"[VALIDATE] 模型验证通过: {model_path} ({file_size / 1024:.1f} KB)")
    return True


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
    1. 默认超时从 24 小时改为 2 小时（训练通常不会这么久）
    2. 检查锁持有进程是否存活，如果进程已死则清理锁
    3. 记录更详细的锁信息
    4. 如果锁是调度器创建的，子进程可以继承使用
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
            # 解析锁文件
            lock_info = _parse_lock_file(lock_path)
            lock_pid = int(lock_info.get('pid', 0))
            lock_created = int(lock_info.get('created_at', 0))
            lock_type = lock_info.get('type', '')
            
            # 如果锁是调度器创建的，检查调度器是否是我们的父进程
            if lock_type == 'scheduler':
                parent_pid = os.getppid()
                if lock_pid == parent_pid:
                    # 锁是父进程（调度器）创建的，更新锁信息并继续
                    print(f"[LOCK] 继承调度器的锁 (父进程 PID={parent_pid})")
                    try:
                        with open(lock_path, 'w', encoding='utf-8') as f:
                            f.write(f"pid={os.getpid()}\ncreated_at={int(time.time())}\nhostname={os.environ.get('COMPUTERNAME', os.environ.get('HOSTNAME', 'unknown'))}\ntype=subprocess\nparent_pid={parent_pid}\n")
                        return True
                    except Exception:
                        pass
            
            # 检查锁是否过期
            if lock_created > 0 and (time.time() - lock_created) > stale_seconds:
                print(f"[LOCK] 锁已过期 ({(time.time() - lock_created) / 3600:.1f} 小时)，清理中...")
                os.remove(lock_path)
                return _acquire_file_lock(lock_path, stale_seconds=stale_seconds)
            
            # 检查持有锁的进程是否存活
            if lock_pid > 0 and not _is_process_alive(lock_pid):
                print(f"[LOCK] 锁持有进程 (PID={lock_pid}) 已不存在，清理中...")
                os.remove(lock_path)
                return _acquire_file_lock(lock_path, stale_seconds=stale_seconds)
            
            # 锁有效且进程存活
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
    """
    保存训练状态到文件
    
    status: 'started', 'completed', 'failed'
    """
    import json
    status_path = _training_status_path(profile)
    status_data = {
        'status': status,
        'timestamp': int(time.time()),
        'timestamp_str': time.strftime('%Y-%m-%d %H:%M:%S'),
        'pid': os.getpid(),
        'model_path': profile.get_fasttext_model_path(),
    }
    if error:
        status_data['error'] = str(error)[:500]  # 限制错误信息长度
    
    try:
        with open(status_path, 'w', encoding='utf-8') as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARNING] 无法保存训练状态: {e}")


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/train_fasttext_model.py <profile_name>")
        print("示例: python tools/train_fasttext_model.py default")
        sys.exit(1)
    
    profile_name = sys.argv[1]
    
    print(f"{'='*60}")
    print(f"fastText 模型训练工具")
    print(f"配置: {profile_name}")
    print(f"{'='*60}\n")
    
    # 加载配置
    profile = ModerationProfile(profile_name)
    
    # 显示配置信息
    cfg = profile.config.fasttext_training
    print(f"训练配置:")
    print(f"  最小样本数: {cfg.min_samples}")
    print(f"  最大样本数: {cfg.max_samples}")
    print(f"  样本加载策略(sample_loading): {cfg.sample_loading}")
    print(f"  使用 jieba 分词: {cfg.use_jieba}")
    print(f"  使用 tiktoken 分词: {cfg.use_tiktoken}")
    if cfg.use_tiktoken:
        print(f"  tiktoken 模型: {cfg.tiktoken_model}")
    print(f"  维度: {cfg.dim}")
    print(f"  学习率: {cfg.lr}")
    print(f"  训练轮数: {cfg.epoch}")
    print(f"  词级 n-gram: {cfg.word_ngrams}")
    
    # 分词模式说明
    if cfg.use_tiktoken and cfg.use_jieba:
        print(f"  分词模式: tiktoken + jieba 组合（实验性）")
        print(f"  字符级 n-gram: 关闭")
    elif cfg.use_tiktoken:
        print(f"  分词模式: tiktoken BPE 分词")
        print(f"  字符级 n-gram: 关闭")
    elif cfg.use_jieba:
        print(f"  分词模式: jieba 中文分词")
        print(f"  字符级 n-gram: 关闭")
    else:
        print(f"  分词模式: 字符级 n-gram（原版）")
        print(f"  字符级 n-gram: [{cfg.minn}, {cfg.maxn}]")
    print()
    
    # 检查样本数据
    storage = SampleStorage(profile.get_db_path())
    sample_count = storage.get_sample_count()
    pass_count, violation_count = storage.get_label_counts()
    
    print(f"样本统计:")
    print(f"  总数: {sample_count}")
    print(f"  通过: {pass_count}")
    print(f"  违规: {violation_count}")
    print()
    
    if sample_count < cfg.min_samples:
        print(f"❌ 样本数不足 {cfg.min_samples}，无法训练")
        sys.exit(1)
    
    # 开始训练（根据配置选择版本）
    if cfg.use_jieba or cfg.use_tiktoken:
        # 使用高级分词版本
        if cfg.use_tiktoken and cfg.use_jieba:
            mode_desc = "tiktoken + jieba 组合分词（实验性）"
        elif cfg.use_tiktoken:
            mode_desc = f"tiktoken 分词 (模型: {cfg.tiktoken_model})"
        else:
            mode_desc = "jieba 分词"
        
        print(f"开始训练（{mode_desc}）...\n")
        train_func = train_fasttext_model_jieba
    else:
        print(f"开始训练（使用字符级 n-gram）...\n")
        train_func = train_fasttext_model
    
    lock_path = _training_lock_path(profile)
    if not _acquire_file_lock(lock_path):
        print(f"❌ 当前配置正在训练中（文件锁存在）: {lock_path}")
        sys.exit(2)

    # 记录训练开始状态
    _save_training_status(profile, 'started')

    try:
        train_func(profile)
        
        # 验证模型文件是否有效
        model_path = profile.get_fasttext_model_path()
        if not _validate_model_file(model_path):
            raise RuntimeError(f"训练后模型文件验证失败: {model_path}")
        
        # 记录训练完成状态
        _save_training_status(profile, 'completed')
        
        print(f"\n✅ 训练完成")
        print(f"模型已保存: {model_path}")

        # 提示信息
        if cfg.use_tiktoken and cfg.use_jieba:
            print(f"\n💡 提示: 使用了 tiktoken + jieba 组合分词（实验性功能）")
        elif cfg.use_tiktoken:
            print(f"\n💡 提示: 使用了 tiktoken BPE 分词")
        elif cfg.use_jieba:
            print(f"\n💡 提示: 使用了 jieba 分词，更适合中文文本")
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