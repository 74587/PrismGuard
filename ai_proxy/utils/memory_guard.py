"""
超强GC内存守护模块 - 自动监控并清理超大容器
"""
import gc
import sys
import os
import threading
import weakref
import ctypes
import psutil
from typing import Dict, List, Any, Optional
from collections.abc import MutableMapping, MutableSequence


def malloc_trim() -> bool:
    """
    调用 glibc 的 malloc_trim(0) 强制释放空闲内存给操作系统
    
    解决问题：Python/glibc 的内存分配器会保留已释放的内存在 arena 中，
    导致 RSS 不下降、swap 持续增长。malloc_trim 强制归还这些内存。
    
    Returns:
        是否成功调用
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        result = libc.malloc_trim(0)
        return result == 1
    except (OSError, AttributeError):
        # 非 Linux 或 glibc 不可用
        return False


def release_memory() -> None:
    """
    强制释放内存：先 GC 回收 Python 对象，再调用 malloc_trim 归还给 OS
    
    适用场景：
    - 模型更新后删除旧模型
    - 大量临时对象处理完成后
    - 定期内存清理
    """
    # 先执行 Python GC
    gc.collect()
    gc.collect()  # 多次调用确保循环引用被清理
    
    # 再调用 malloc_trim 归还给 OS
    if malloc_trim():
        print("[MEMORY] malloc_trim() 成功，空闲内存已归还 OS")
    else:
        print("[MEMORY] malloc_trim() 不可用（非 glibc 环境）")


class MemoryGuard:
    """内存守护器 - 监控并自动清理超大容器"""
    
    # 阈值：1GB = 1024 * 1024 * 1024 bytes
    SIZE_THRESHOLD = 1024 * 1024 * 1024
    
    def __init__(self):
        self._tracked_objects: Dict[int, weakref.ref] = {}
        self._lock = threading.Lock()
        self._enabled = True
    
    def get_size(self, obj: Any) -> int:
        """递归计算对象占用的内存大小（估算）"""
        try:
            size = sys.getsizeof(obj)
            
            # 如果是字典，累加所有键值对的大小
            if isinstance(obj, dict):
                for key, value in obj.items():
                    size += sys.getsizeof(key)
                    size += sys.getsizeof(value)
                    # 对于嵌套的容器，递归计算（限制深度避免循环引用）
                    if isinstance(value, (dict, list, set, tuple)):
                        try:
                            size += self._get_container_size(value, depth=1, max_depth=3)
                        except:
                            pass
            
            # 如果是列表，累加所有元素的大小
            elif isinstance(obj, list):
                for item in obj:
                    size += sys.getsizeof(item)
                    if isinstance(item, (dict, list, set, tuple)):
                        try:
                            size += self._get_container_size(item, depth=1, max_depth=3)
                        except:
                            pass
            
            return size
        except:
            return sys.getsizeof(obj)
    
    def _get_container_size(self, obj: Any, depth: int, max_depth: int) -> int:
        """递归计算嵌套容器大小（带深度限制）"""
        if depth >= max_depth:
            return 0
        
        size = 0
        try:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    size += sys.getsizeof(key) + sys.getsizeof(value)
                    if isinstance(value, (dict, list, set, tuple)):
                        size += self._get_container_size(value, depth + 1, max_depth)
            elif isinstance(obj, (list, tuple, set)):
                for item in obj:
                    size += sys.getsizeof(item)
                    if isinstance(item, (dict, list, set, tuple)):
                        size += self._get_container_size(item, depth + 1, max_depth)
        except:
            pass
        
        return size
    
    def check_and_clear(self, container: Any, name: str = "unknown") -> bool:
        """
        检查容器大小，如果超过阈值则清空
        
        Args:
            container: 要检查的容器（dict 或 list）
            name: 容器名称（用于日志）
        
        Returns:
            是否执行了清空操作
        """
        if not self._enabled:
            return False
        
        try:
            size = self.get_size(container)
            size_mb = size / (1024 * 1024)
            
            # 如果超过阈值，强制清空
            if size >= self.SIZE_THRESHOLD:
                print(f"\n{'='*60}")
                print(f"[MEMORY_GUARD] ⚠️ 检测到超大容器！")
                print(f"  名称: {name}")
                print(f"  类型: {type(container).__name__}")
                print(f"  大小: {size_mb:.2f} MB")
                print(f"  元素数量: {len(container) if hasattr(container, '__len__') else 'N/A'}")
                print(f"  操作: 强制清空")
                print(f"{'='*60}\n")
                
                # 根据类型清空
                if isinstance(container, MutableMapping):
                    container.clear()
                elif isinstance(container, MutableSequence):
                    container.clear()
                
                # 强制垃圾回收
                gc.collect()
                
                return True
            
            # 如果超过500MB，发出警告但不清空
            elif size >= 500 * 1024 * 1024:
                print(f"[MEMORY_GUARD] ⚠️ 容器 '{name}' 占用 {size_mb:.2f} MB (警告阈值)")
            
            return False
            
        except Exception as e:
            print(f"[MEMORY_GUARD] 检查容器失败: {e}")
            return False
    
    def track(self, container: Any, name: str = "unknown"):
        """
        注册容器进行持续监控
        
        Args:
            container: 要追踪的容器
            name: 容器名称
        """
        if not self._enabled:
            return
        
        try:
            obj_id = id(container)
            with self._lock:
                # 使用弱引用避免阻止垃圾回收
                self._tracked_objects[obj_id] = (weakref.ref(container), name)
        except:
            pass
    
    def check_all_tracked(self) -> int:
        """
        检查所有被追踪的容器，返回清空的数量
        """
        if not self._enabled:
            return 0
        
        cleared_count = 0
        to_remove = []
        
        with self._lock:
            for obj_id, (ref, name) in list(self._tracked_objects.items()):
                container = ref()
                
                # 如果对象已被回收，从追踪列表移除
                if container is None:
                    to_remove.append(obj_id)
                    continue
                
                # 检查并可能清空
                if self.check_and_clear(container, name):
                    cleared_count += 1
            
            # 清理已回收的对象
            for obj_id in to_remove:
                del self._tracked_objects[obj_id]
        
        return cleared_count
    
    def enable(self):
        """启用内存守护"""
        self._enabled = True
        print("[MEMORY_GUARD] 内存守护已启用")
    
    def disable(self):
        """禁用内存守护"""
        self._enabled = False
        print("[MEMORY_GUARD] 内存守护已禁用")


# 全局单例
_memory_guard: Optional[MemoryGuard] = None
_guard_lock = threading.Lock()


def get_memory_guard() -> MemoryGuard:
    """获取全局内存守护器实例"""
    global _memory_guard
    
    with _guard_lock:
        if _memory_guard is None:
            _memory_guard = MemoryGuard()
        return _memory_guard


def check_container(container: Any, name: str = "unknown") -> bool:
    """
    便捷函数：检查并可能清空容器
    
    Example:
        from ai_proxy.utils.memory_guard import check_container
        
        my_cache = {}
        # ... 使用缓存
        check_container(my_cache, "my_cache")
    """
    guard = get_memory_guard()
    return guard.check_and_clear(container, name)


def track_container(container: Any, name: str = "unknown"):
    """
    便捷函数：注册容器进行持续监控
    
    Example:
        from ai_proxy.utils.memory_guard import track_container
        
        my_cache = {}
        track_container(my_cache, "my_cache")
    """
    guard = get_memory_guard()
    guard.track(container, name)


def check_all_tracked() -> int:
    """
    便捷函数：检查所有被追踪的容器
    
    Returns:
        清空的容器数量
    """
    guard = get_memory_guard()
    return guard.check_all_tracked()


# 装饰器：自动监控函数中的容器
def guard_containers(*container_names):
    """
    装饰器：自动监控函数局部变量中的容器
    
    Example:
        @guard_containers('cache', 'buffer')
        def my_function():
            cache = {}
            buffer = []
            # ... 使用容器
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            
            # 检查函数局部变量中的容器
            frame = sys._getframe()
            local_vars = frame.f_locals
            
            for name in container_names:
                if name in local_vars:
                    check_container(local_vars[name], f"{func.__name__}.{name}")
            
            return result
        return wrapper
    return decorator


class ProcessMemoryMonitor:
    """进程总内存监控器 - 兜底机制"""
    
    # 内存阈值：2GB
    MEMORY_LIMIT = 2 * 1024 * 1024 * 1024
    
    def __init__(self):
        self._enabled = True
        self._process = psutil.Process(os.getpid())
    
    def get_memory_usage(self) -> int:
        """获取当前进程内存使用（字节）"""
        try:
            mem_info = self._process.memory_info()
            return mem_info.rss  # Resident Set Size - 实际物理内存占用
        except:
            return 0
    
    def check_and_force_exit(self) -> bool:
        """
        检查进程总内存，如果超过阈值则强制退出
        
        Returns:
            是否触发强制退出
        """
        if not self._enabled:
            return False
        
        try:
            mem_usage = self.get_memory_usage()
            mem_mb = mem_usage / (1024 * 1024)
            mem_gb = mem_usage / (1024 * 1024 * 1024)
            
            # 如果超过阈值，强制退出
            if mem_usage >= self.MEMORY_LIMIT:
                print(f"\n{'='*60}")
                print(f"[MEMORY_MONITOR] 🔴 进程内存超限！强制退出")
                print(f"  进程 PID: {os.getpid()}")
                print(f"  内存使用: {mem_gb:.2f} GB ({mem_mb:.0f} MB)")
                print(f"  内存限制: {self.MEMORY_LIMIT / (1024**3):.1f} GB")
                print(f"  操作: 立即终止进程")
                print(f"{'='*60}\n")
                
                # 尝试优雅关闭
                try:
                    import signal
                    os.kill(os.getpid(), signal.SIGTERM)
                except:
                    pass
                
                # 强制退出
                os._exit(1)
                return True
            
            # 警告级别：1.5GB
            elif mem_usage >= 1.5 * 1024 * 1024 * 1024:
                print(f"[MEMORY_MONITOR] ⚠️ 进程内存接近限制: {mem_gb:.2f} GB / 2.0 GB")
            
            return False
            
        except Exception as e:
            print(f"[MEMORY_MONITOR] 检查失败: {e}")
            return False
    
    def enable(self):
        """启用内存监控"""
        self._enabled = True
        print("[MEMORY_MONITOR] 进程内存监控已启用")
    
    def disable(self):
        """禁用内存监控"""
        self._enabled = False
        print("[MEMORY_MONITOR] 进程内存监控已禁用")


# 全局进程内存监控器
_process_monitor: Optional[ProcessMemoryMonitor] = None
_monitor_lock = threading.Lock()


def get_process_monitor() -> ProcessMemoryMonitor:
    """获取全局进程内存监控器实例"""
    global _process_monitor
    
    with _monitor_lock:
        if _process_monitor is None:
            _process_monitor = ProcessMemoryMonitor()
        return _process_monitor


def check_process_memory() -> bool:
    """
    便捷函数：检查进程总内存并可能强制退出
    
    Returns:
        是否触发强制退出
    """
    monitor = get_process_monitor()
    return monitor.check_and_force_exit()


def periodic_memory_cleanup() -> None:
    """
    定期内存清理：GC + malloc_trim
    
    建议在后台循环中定期调用（如每 30-60 秒）
    """
    gc.collect()
    malloc_trim()