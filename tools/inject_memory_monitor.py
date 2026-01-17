#!/usr/bin/env python3
"""
内存监控注入工具
在运行中的 FastAPI 应用中添加内存监控端点
"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, PlainTextResponse
import tracemalloc
import gc
import psutil
from typing import Dict, List
from collections import defaultdict


def inject_memory_endpoints(app: FastAPI):
    """向 FastAPI 应用注入内存监控端点"""
    
    # 启动 tracemalloc
    if not tracemalloc.is_tracing():
        tracemalloc.start()
        print("[MEMORY_MONITOR] tracemalloc 已启动")
    
    @app.get("/_debug/memory/status")
    async def memory_status():
        """获取内存状态概览"""
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        
        # tracemalloc 信息
        current, peak = tracemalloc.get_traced_memory()
        
        # GC 信息
        gc_stats = gc.get_stats()
        gc_count = gc.get_count()
        
        return {
            "process": {
                "pid": os.getpid(),
                "rss_mb": mem_info.rss / (1024 * 1024),
                "vms_mb": mem_info.vms / (1024 * 1024),
                "percent": process.memory_percent(),
                "num_threads": process.num_threads(),
            },
            "tracemalloc": {
                "current_mb": current / (1024 * 1024),
                "peak_mb": peak / (1024 * 1024),
                "is_tracing": tracemalloc.is_tracing(),
            },
            "gc": {
                "count": gc_count,
                "stats": gc_stats,
                "garbage_count": len(gc.garbage),
            }
        }
    
    @app.get("/_debug/memory/top")
    async def memory_top(limit: int = 20):
        """获取 Top N 内存分配"""
        if not tracemalloc.is_tracing():
            return {"error": "tracemalloc not started"}
        
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics('lineno')
        
        results = []
        for stat in top_stats[:limit]:
            results.append({
                "file": stat.traceback.format()[0] if stat.traceback else "unknown",
                "size_kb": stat.size / 1024,
                "count": stat.count,
            })
        
        return {"top_allocations": results}
    
    @app.get("/_debug/memory/objects")
    async def memory_objects(limit: int = 20):
        """获取对象类型分布"""
        gc.collect()
        
        type_counts = defaultdict(int)
        type_sizes = defaultdict(int)
        
        for obj in gc.get_objects():
            obj_type = type(obj).__name__
            type_counts[obj_type] += 1
            try:
                type_sizes[obj_type] += sys.getsizeof(obj)
            except:
                pass
        
        # 按大小排序
        results = []
        for obj_type in sorted(type_sizes.keys(), key=lambda x: type_sizes[x], reverse=True)[:limit]:
            results.append({
                "type": obj_type,
                "count": type_counts[obj_type],
                "size_mb": type_sizes[obj_type] / (1024 * 1024),
            })
        
        return {"object_types": results}
    
    @app.get("/_debug/memory/containers")
    async def memory_containers():
        """获取容器统计"""
        gc.collect()
        
        stats = {
            'dict': {'count': 0, 'total_size': 0, 'total_items': 0},
            'list': {'count': 0, 'total_size': 0, 'total_items': 0},
            'set': {'count': 0, 'total_size': 0, 'total_items': 0},
        }
        
        for obj in gc.get_objects():
            obj_type = type(obj).__name__
            if obj_type in stats:
                try:
                    size = sys.getsizeof(obj)
                    stats[obj_type]['count'] += 1
                    stats[obj_type]['total_size'] += size
                    if hasattr(obj, '__len__'):
                        stats[obj_type]['total_items'] += len(obj)
                except:
                    pass
        
        # 转换为 MB
        for container_type in stats:
            stats[container_type]['total_size_mb'] = stats[container_type]['total_size'] / (1024 * 1024)
            if stats[container_type]['count'] > 0:
                stats[container_type]['avg_items'] = stats[container_type]['total_items'] / stats[container_type]['count']
            else:
                stats[container_type]['avg_items'] = 0
        
        return {"containers": stats}
    
    @app.post("/_debug/memory/gc")
    async def trigger_gc():
        """触发垃圾回收"""
        before = psutil.Process(os.getpid()).memory_info().rss
        
        collected = gc.collect()
        
        after = psutil.Process(os.getpid()).memory_info().rss
        freed_mb = (before - after) / (1024 * 1024)
        
        return {
            "collected_objects": collected,
            "freed_mb": freed_mb,
            "rss_before_mb": before / (1024 * 1024),
            "rss_after_mb": after / (1024 * 1024),
        }
    
    @app.post("/_debug/memory/snapshot")
    async def take_snapshot():
        """拍摄内存快照"""
        if not tracemalloc.is_tracing():
            return {"error": "tracemalloc not started"}
        
        snapshot = tracemalloc.take_snapshot()
        
        # 保存快照到文件
        import time
        filename = f"memory_snapshot_{int(time.time())}.pickle"
        filepath = os.path.join("logs", filename)
        
        os.makedirs("logs", exist_ok=True)
        
        import pickle
        with open(filepath, 'wb') as f:
            pickle.dump(snapshot, f)
        
        return {
            "snapshot_saved": filepath,
            "timestamp": time.time(),
        }
    
    @app.get("/_debug/memory/leaks")
    async def check_leaks():
        """检查内存泄漏"""
        # 强制垃圾回收
        collected = gc.collect()
        
        # 检查无法回收的对象
        garbage_count = len(gc.garbage)
        
        # 检查引用循环
        gc.set_debug(gc.DEBUG_SAVEALL)
        collected_with_debug = gc.collect()
        cycles_count = len(gc.garbage) - garbage_count
        gc.set_debug(0)
        
        return {
            "collected_objects": collected,
            "garbage_objects": garbage_count,
            "reference_cycles": cycles_count,
            "has_leaks": garbage_count > 0 or cycles_count > 0,
        }
    
    @app.get("/_debug/memory/caches")
    async def check_caches():
        """检查应用缓存状态"""
        from ai_proxy.moderation.smart.bow import _model_cache as bow_cache
        from ai_proxy.moderation.smart.fasttext_model import _fasttext_cache
        from ai_proxy.moderation.smart.scheduler import _profile_locks
        
        return {
            "bow_model_cache": {
                "profiles": list(bow_cache.keys()),
                "count": len(bow_cache),
            },
            "fasttext_cache": {
                "profiles": list(_fasttext_cache.keys()),
                "count": len(_fasttext_cache),
            },
            "profile_locks": {
                "profiles": list(_profile_locks.keys()),
                "count": len(_profile_locks),
            }
        }
    
    @app.post("/_debug/memory/clear_caches")
    async def clear_caches():
        """清空应用缓存"""
        from ai_proxy.moderation.smart.bow import _model_cache as bow_cache
        from ai_proxy.moderation.smart.fasttext_model import _fasttext_cache
        from ai_proxy.utils.memory_guard import release_memory
        
        bow_count = len(bow_cache)
        fasttext_count = len(_fasttext_cache)
        
        bow_cache.clear()
        _fasttext_cache.clear()
        
        # 强制释放内存
        release_memory()
        
        return {
            "cleared": {
                "bow_models": bow_count,
                "fasttext_models": fasttext_count,
            }
        }
    
    print("[MEMORY_MONITOR] 内存监控端点已注入")
    print("  - GET  /_debug/memory/status      - 内存状态概览")
    print("  - GET  /_debug/memory/top         - Top N 内存分配")
    print("  - GET  /_debug/memory/objects     - 对象类型分布")
    print("  - GET  /_debug/memory/containers  - 容器统计")
    print("  - GET  /_debug/memory/caches      - 应用缓存状态")
    print("  - GET  /_debug/memory/leaks       - 内存泄漏检测")
    print("  - POST /_debug/memory/gc          - 触发垃圾回收")
    print("  - POST /_debug/memory/snapshot    - 拍摄内存快照")
    print("  - POST /_debug/memory/clear_caches - 清空应用缓存")


if __name__ == "__main__":
    print("此模块应该在应用启动时导入")
    print("\n在 ai_proxy/app.py 中添加:")
    print("  from tools.inject_memory_monitor import inject_memory_endpoints")
    print("  inject_memory_endpoints(app)")
