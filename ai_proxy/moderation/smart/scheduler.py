"""
定时任务调度器 - 自动训练本地模型（子进程模式）
- 训练改为启动独立子进程执行 tools/train_*.py
- 子进程使用与主进程一致的 Python：必须用 sys.executable（兼容 conda/venv/uv 等环境）
- 子进程继承主进程 env（API Key 等环境变量无需重复配置）
- 主进程不再承载训练期间的峰值内存，子进程退出后由 OS 回收
- 跨进程互斥交给训练脚本的 .train.lock（避免父进程自锁）
"""
import os
import sys
import asyncio
import time
from datetime import datetime
from typing import List, Dict

from ai_proxy.moderation.smart.profile import ModerationProfile, LocalModelType
from ai_proxy.moderation.smart.storage import SampleStorage

# 记录每个 profile 的训练锁（进程内），避免重复训练
_profile_locks: Dict[str, asyncio.Lock] = {}


def get_profile_lock(profile_name: str) -> asyncio.Lock:
    """获取对应 profile 的训练锁（进程内）"""
    if profile_name not in _profile_locks:
        _profile_locks[profile_name] = asyncio.Lock()
    return _profile_locks[profile_name]


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

            if not should_train(profile):
                storage = SampleStorage(profile.get_db_path())
                sample_count = storage.get_sample_count()
                print(f"[SCHEDULER] 跳过训练: {profile_name} (样本数={sample_count})")
                continue

            lock = get_profile_lock(profile_name)
            if lock.locked():
                print(f"[SCHEDULER] {profile_name} 正在训练中（进程内锁），跳过本次调度")
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