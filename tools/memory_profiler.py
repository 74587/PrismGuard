#!/usr/bin/env python3
"""
Python 进程内存分析工具
用于诊断运行中的 Python 进程的内存使用情况
"""
import os
import sys
import time
import psutil
import gc
from typing import Dict, List, Tuple
from collections import defaultdict


def get_process_memory_info(pid: int = None) -> Dict:
    """获取进程内存信息"""
    if pid is None:
        pid = os.getpid()
    
    try:
        process = psutil.Process(pid)
        mem_info = process.memory_info()
        mem_percent = process.memory_percent()
        
        return {
            'pid': pid,
            'rss': mem_info.rss,  # 实际物理内存
            'vms': mem_info.vms,  # 虚拟内存
            'rss_mb': mem_info.rss / (1024 * 1024),
            'vms_mb': mem_info.vms / (1024 * 1024),
            'percent': mem_percent,
            'num_threads': process.num_threads(),
        }
    except psutil.NoSuchProcess:
        return None


def analyze_object_types() -> List[Tuple[str, int, int]]:
    """分析 Python 对象类型分布"""
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
    
    # 按内存占用排序
    results = []
    for obj_type in type_counts:
        results.append((
            obj_type,
            type_counts[obj_type],
            type_sizes[obj_type]
        ))
    
    results.sort(key=lambda x: x[2], reverse=True)
    return results


def find_large_objects(min_size_mb: float = 1.0) -> List[Tuple[str, int, str]]:
    """查找大对象"""
    gc.collect()
    
    min_size_bytes = int(min_size_mb * 1024 * 1024)
    large_objects = []
    
    for obj in gc.get_objects():
        try:
            size = sys.getsizeof(obj)
            if size >= min_size_bytes:
                obj_type = type(obj).__name__
                obj_repr = repr(obj)[:100]
                large_objects.append((obj_type, size, obj_repr))
        except:
            pass
    
    large_objects.sort(key=lambda x: x[1], reverse=True)
    return large_objects


def analyze_containers() -> Dict:
    """分析容器对象（dict, list, set）"""
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
    
    return stats


def print_memory_report():
    """打印完整内存报告"""
    print("=" * 80)
    print("Python 进程内存分析报告")
    print("=" * 80)
    print()
    
    # 1. 进程级内存信息
    print("【1】进程内存使用")
    print("-" * 80)
    mem_info = get_process_memory_info()
    if mem_info:
        print(f"  PID: {mem_info['pid']}")
        print(f"  RSS (实际物理内存): {mem_info['rss_mb']:.2f} MB")
        print(f"  VMS (虚拟内存): {mem_info['vms_mb']:.2f} MB")
        print(f"  内存占用率: {mem_info['percent']:.2f}%")
        print(f"  线程数: {mem_info['num_threads']}")
    print()
    
    # 2. Python 对象类型分布（Top 20）
    print("【2】Python 对象类型分布 (Top 20)")
    print("-" * 80)
    print(f"{'类型':<30} {'数量':>10} {'总大小(MB)':>15}")
    print("-" * 80)
    
    obj_types = analyze_object_types()
    for obj_type, count, size in obj_types[:20]:
        size_mb = size / (1024 * 1024)
        print(f"{obj_type:<30} {count:>10} {size_mb:>15.2f}")
    print()
    
    # 3. 容器统计
    print("【3】容器对象统计")
    print("-" * 80)
    containers = analyze_containers()
    for container_type, stats in containers.items():
        count = stats['count']
        total_size_mb = stats['total_size'] / (1024 * 1024)
        total_items = stats['total_items']
        avg_items = total_items / count if count > 0 else 0
        print(f"  {container_type}:")
        print(f"    数量: {count}")
        print(f"    总大小: {total_size_mb:.2f} MB")
        print(f"    总元素数: {total_items}")
        print(f"    平均元素数: {avg_items:.1f}")
    print()
    
    # 4. 大对象（>1MB）
    print("【4】大对象列表 (>1MB)")
    print("-" * 80)
    print(f"{'类型':<20} {'大小(MB)':>12} {'预览':<50}")
    print("-" * 80)
    
    large_objs = find_large_objects(min_size_mb=1.0)
    if large_objs:
        for obj_type, size, obj_repr in large_objs[:20]:
            size_mb = size / (1024 * 1024)
            print(f"{obj_type:<20} {size_mb:>12.2f} {obj_repr:<50}")
    else:
        print("  (未发现 >1MB 的对象)")
    print()
    
    # 5. GC 统计
    print("【5】垃圾回收统计")
    print("-" * 80)
    gc_stats = gc.get_stats()
    for i, gen_stats in enumerate(gc_stats):
        print(f"  Generation {i}:")
        print(f"    collections: {gen_stats.get('collections', 0)}")
        print(f"    collected: {gen_stats.get('collected', 0)}")
        print(f"    uncollectable: {gen_stats.get('uncollectable', 0)}")
    
    gc_count = gc.get_count()
    print(f"  当前对象计数: {gc_count}")
    print()
    
    print("=" * 80)


def monitor_memory_continuous(interval_seconds: int = 5, duration_minutes: int = 10):
    """持续监控内存使用"""
    print(f"开始持续监控 (间隔={interval_seconds}秒, 持续={duration_minutes}分钟)")
    print("=" * 80)
    print(f"{'时间':<20} {'RSS(MB)':>12} {'VMS(MB)':>12} {'占用率(%)':>12} {'线程数':>10}")
    print("=" * 80)
    
    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)
    
    baseline_rss = None
    
    try:
        while time.time() < end_time:
            mem_info = get_process_memory_info()
            if mem_info:
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                rss_mb = mem_info['rss_mb']
                vms_mb = mem_info['vms_mb']
                percent = mem_info['percent']
                threads = mem_info['num_threads']
                
                if baseline_rss is None:
                    baseline_rss = rss_mb
                
                growth = rss_mb - baseline_rss
                growth_str = f"(+{growth:.1f})" if growth > 0 else f"({growth:.1f})"
                
                print(f"{timestamp:<20} {rss_mb:>12.2f} {vms_mb:>12.2f} {percent:>12.2f} {threads:>10} {growth_str}")
            
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\n监控已停止")
    
    print("=" * 80)


def attach_to_process(pid: int, interval_seconds: int = 5):
    """附加到指定进程进行监控"""
    print(f"附加到进程 PID={pid}")
    print("=" * 80)
    print(f"{'时间':<20} {'RSS(MB)':>12} {'VMS(MB)':>12} {'占用率(%)':>12} {'线程数':>10}")
    print("=" * 80)
    
    baseline_rss = None
    
    try:
        while True:
            mem_info = get_process_memory_info(pid)
            if mem_info is None:
                print(f"进程 {pid} 不存在或已退出")
                break
            
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            rss_mb = mem_info['rss_mb']
            vms_mb = mem_info['vms_mb']
            percent = mem_info['percent']
            threads = mem_info['num_threads']
            
            if baseline_rss is None:
                baseline_rss = rss_mb
            
            growth = rss_mb - baseline_rss
            growth_str = f"(+{growth:.1f})" if growth > 0 else f"({growth:.1f})"
            
            print(f"{timestamp:<20} {rss_mb:>12.2f} {vms_mb:>12.2f} {percent:>12.2f} {threads:>10} {growth_str}")
            
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\n监控已停止")
    
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Python 进程内存分析工具")
    parser.add_argument("--report", action="store_true", help="生成当前进程的内存报告")
    parser.add_argument("--monitor", action="store_true", help="持续监控当前进程")
    parser.add_argument("--attach", type=int, metavar="PID", help="附加到指定 PID 进行监控")
    parser.add_argument("--interval", type=int, default=5, help="监控间隔（秒），默认5秒")
    parser.add_argument("--duration", type=int, default=10, help="监控持续时间（分钟），默认10分钟")
    
    args = parser.parse_args()
    
    if args.report:
        print_memory_report()
    elif args.monitor:
        monitor_memory_continuous(args.interval, args.duration)
    elif args.attach:
        attach_to_process(args.attach, args.interval)
    else:
        parser.print_help()
        print("\n示例用法:")
        print("  1. 生成当前进程内存报告:")
        print("     python tools/memory_profiler.py --report")
        print()
        print("  2. 持续监控当前进程 (5秒间隔, 10分钟):")
        print("     python tools/memory_profiler.py --monitor --interval 5 --duration 10")
        print()
        print("  3. 附加到指定进程进行监控:")
        print("     python tools/memory_profiler.py --attach 12345 --interval 5")
