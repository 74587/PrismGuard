"""
定时任务调度器 - 自动训练本地模型（子进程模式）
- 训练改为启动独立子进程执行 tools/train_*.py
- 子进程使用与主进程一致的 Python：必须用 sys.executable（兼容 conda/venv/uv 等环境）
- 子进程继承主进程 env（API Key 等环境变量无需重复配置）
- 主进程不再承载训练期间的峰值内存，子进程退出后由 OS 回收
- 跨进程互斥交给训练脚本的 .train.lock（避免父进程自锁）

改进：
- 添加训练状态检查，避免加载损坏的模型
- 锁超时从 24 小时改为 2 小时
- 检查锁持有进程是否存活
"""
import os
import sys
import json
import asyncio
import time
from datetime import datetime
from typing import List, Dict, Optional

from ai_proxy.moderation.smart.profile import ModerationProfile, LocalModelType
from ai_proxy.moderation.smart.storage import SampleStorage

# 记录每个 profile 的训练锁（进程内），避免重复训练
_profile_locks: Dict[str, asyncio.Lock] = {}


def get_profile_lock(profile_name: str) -> asyncio.Lock:
    """获取对应 profile 的训练锁（进程内）"""
    if profile_name not in _profile_locks:
        _profile_locks[profile_name] = asyncio.Lock()
    return _profile_locks[profile_name]


def _training_status_path(profile: ModerationProfile) -> str:
    """训练状态文件路径"""
    return os.path.join(profile.base_dir, ".train_status.json")


def _training_lock_path(profile: ModerationProfile) -> str:
    """训练锁文件路径"""
    return os.path.join(profile.base_dir, ".train.lock")


def get_training_status(profile: ModerationProfile) -> Optional[Dict]:
    """
    获取训练状态
    
    Returns:
        状态字典 {'status': 'completed'|'failed'|'started', 'timestamp': int, 'error': str|None}
        如果状态文件不存在返回 None
    """
    status_path = _training_status_path(profile)
    if not os.path.exists(status_path):
        return None
    
    try:
        with open(status_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def check_stale_lock(profile: ModerationProfile, stale_seconds: int = 2 * 3600) -> bool:
    """
    检查并清理过期的锁文件
    
    改进：
    1. 默认超时从 24 小时改为 2 小时
    2. 检查锁持有进程是否存活
    
    Returns:
        True 如果锁被清理或不存在，False 如果锁有效
    """
    lock_path = _training_lock_path(profile)
    
    if not os.path.exists(lock_path):
        return True
    
    try:
        # 读取锁文件内容
        with open(lock_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        lock_info = {}
        for line in content.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                lock_info[key.strip()] = value.strip()
        
        lock_pid = int(lock_info.get('pid', 0))
        lock_created = int(lock_info.get('created_at', 0))
        
        # 检查锁是否过期
        if lock_created > 0 and (time.time() - lock_created) > stale_seconds:
            print(f"[SCHEDULER] 锁已过期 ({(time.time() - lock_created) / 3600:.1f} 小时)，清理中...")
            os.remove(lock_path)
            return True
        
        # 检查持有锁的进程是否存活
        if lock_pid > 0:
            try:
                if sys.platform == 'win32':
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x1000, False, lock_pid)
                    if handle:
                        kernel32.CloseHandle(handle)
                        # 进程存活，锁有效
                        return False
                    # 进程不存在
                    print(f"[SCHEDULER] 锁持有进程 (PID={lock_pid}) 已不存在，清理中...")
                    os.remove(lock_path)
                    return True
                else:
                    os.kill(lock_pid, 0)
                    # 进程存活，锁有效
                    return False
            except (OSError, ProcessLookupError):
                # 进程不存在
                print(f"[SCHEDULER] 锁持有进程 (PID={lock_pid}) 已不存在，清理中...")
                os.remove(lock_path)
                return True
            except Exception:
                pass
        
        return False
        
    except Exception as e:
        print(f"[SCHEDULER] 检查锁状态时出错: {e}")
        return False


def _project_root_dir() -> str:
    """
    返回项目根目录（包含 tools/ 与 ai_proxy/ 的目录）
    scheduler.py 位于 ai_proxy/moderation/smart/
    """
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def get_all_profiles() -> List[str]:
    """扫描所有配置文件夹"""
    base_dir = "configs/mod_profiles"
    if not os.path.exists(base_dir):
        return []

    profiles = []
    for name in os.listdir(base_dir):
        profile_dir = os.path.join(base_dir, name)
        if os.path.isdir(profile_dir):
            config_file = os.path.join(profile_dir, "profile.json")
            if os.path.exists(config_file):
                profiles.append(name)

    return profiles


def _resolve_training_script(profile: ModerationProfile) -> str:
    """
    根据 profile 配置选择训练脚本：
    - fastText：统一用 tools/train_fasttext_model.py（内部会按 use_jieba/use_tiktoken 自动选择）
    - BoW：tools/train_bow_model.py
    """
    root = _project_root_dir()
    if profile.config.local_model_type == LocalModelType.fasttext:
        return os.path.join(root, "tools", "train_fasttext_model.py")
    return os.path.join(root, "tools", "train_bow_model.py")


async def _run_training_subprocess(profile: ModerationProfile) -> int:
    """
    启动训练子进程并将输出实时转发到主进程日志。
    返回子进程 exit code。
    """
    script_path = _resolve_training_script(profile)
    root = _project_root_dir()

    cmd = [sys.executable, "-u", script_path, profile.profile_name]
    print(f"[SCHEDULER] 启动训练子进程: {' '.join(cmd)} (cwd={root})")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=root,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        try:
            text = line.decode("utf-8", errors="replace").rstrip("\n")
        except Exception:
            text = repr(line)
        print(f"[TRAIN:{profile.profile_name}] {text}")

    return await proc.wait()


def should_train(profile: ModerationProfile) -> bool:
    """判断是否需要训练"""
    model_type = profile.config.local_model_type

    # 获取对应模型的配置
    if model_type == LocalModelType.fasttext:
        cfg = profile.config.fasttext_training
        model_path = profile.get_fasttext_model_path()
    else:
        cfg = profile.config.bow_training
        model_path = profile.get_model_path()

    # 检查样本数量
    storage = SampleStorage(profile.get_db_path())
    sample_count = storage.get_sample_count()

    if sample_count < cfg.min_samples:
        return False

    # 检查模型是否存在
    if not profile.local_model_exists():
        return True

    # 检查模型文件修改时间
    model_mtime = os.path.getmtime(model_path)
    interval_seconds = cfg.retrain_interval_minutes * 60

    return (time.time() - model_mtime) > interval_seconds


async def train_all_profiles():
    """训练所有需要训练的配置"""
    profiles = get_all_profiles()

    if not profiles:
        print(f"[SCHEDULER] 未找到配置文件")
        return

    print(f"[SCHEDULER] 扫描到 {len(profiles)} 个配置: {', '.join(profiles)}")

    for profile_name in profiles:
        try:
            profile = ModerationProfile(profile_name)

            # 检查并清理过期锁
            check_stale_lock(profile)

            if not should_train(profile):
                storage = SampleStorage(profile.get_db_path())
                sample_count = storage.get_sample_count()
                print(f"[SCHEDULER] 跳过训练: {profile_name} (样本数={sample_count})")
                continue

            lock = get_profile_lock(profile_name)
            if lock.locked():
                print(f"[SCHEDULER] {profile_name} 正在训练中（进程内锁），跳过本次调度")
                continue

            # 检查上次训练状态
            last_status = get_training_status(profile)
            if last_status and last_status.get('status') == 'failed':
                last_time = last_status.get('timestamp', 0)
                # 如果上次训练失败且在 30 分钟内，跳过（避免频繁重试）
                if time.time() - last_time < 30 * 60:
                    print(f"[SCHEDULER] {profile_name} 上次训练失败，等待冷却期...")
                    continue

            model_type = profile.config.local_model_type
            print(f"[SCHEDULER] 开始训练: {profile_name} (模型类型={model_type.value})")

            async with lock:
                rc = await _run_training_subprocess(profile)

            if rc == 0:
                print(f"[SCHEDULER] 训练完成: {profile_name}")
            elif rc == 2:
                print(f"[SCHEDULER] {profile_name} 正在训练中（子进程检测到文件锁），跳过本次调度")
            else:
                print(f"[SCHEDULER] 训练失败: {profile_name} (exit_code={rc})")

        except Exception as e:
            print(f"[SCHEDULER] 训练失败: {profile_name} - {e}")


async def scheduler_loop(check_interval_minutes: int = 10):
    """定时任务循环"""
    print(f"[SCHEDULER] 启动定时任务，检查间隔: {check_interval_minutes} 分钟")

    while True:
        try:
            print(f"[SCHEDULER] 开始检查 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            await train_all_profiles()
        except Exception as e:
            print(f"[SCHEDULER] 任务执行失败: {e}")

        await asyncio.sleep(check_interval_minutes * 60)


def start_scheduler(check_interval_minutes: int = 10):
    """启动调度器（在后台任务中运行）"""
    asyncio.create_task(scheduler_loop(check_interval_minutes))