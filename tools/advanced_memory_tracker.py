#!/usr/bin/env python3
"""
高级内存追踪工具
使用 tracemalloc 追踪内存分配，使用 objgraph 分析对象引用
"""
import tracemalloc
import sys
import gc
import os
from typing import List, Tuple, Optional


class MemoryTracker:
    """内存追踪器"""
    
    def __init__(self):
        self.snapshots = []
        self.baseline = None
        
    def start(self):
        """开始追踪"""
        tracemalloc.start()
        print("[MemoryTracker] 内存追踪已启动")
        
    def take_snapshot(self, label: str = None):
        """拍摄快照"""
        snapshot = tracemalloc.take_snapshot()
        self.snapshots.append((label or f"snapshot_{len(self.snapshots)}", snapshot))
        print(f"[MemoryTracker] 快照已保存: {label}")
        
        if self.baseline is None:
            self.baseline = snapshot
            print(f"[MemoryTracker] 基线快照已设置")
        
    def compare_snapshots(self, snapshot1_idx: int = 0, snapshot2_idx: int = -1, top_n: int = 20):
        """比较两个快照"""
        if len(self.snapshots) < 2:
            print("[MemoryTracker] 快照数量不足，无法比较")
            return
        
        label1, snap1 = self.snapshots[snapshot1_idx]
        label2, snap2 = self.snapshots[snapshot2_idx]
        
        print("=" * 80)
        print(f"内存快照比较: {label1} -> {label2}")
        print("=" * 80)
        
        top_stats = snap2.compare_to(snap1, 'lineno')
        
        print(f"\n【Top {top_n} 内存增长】")
        print("-" * 80)
        print(f"{'文件:行号':<60} {'增长(KB)':>15}")
        print("-" * 80)
        
        for stat in top_stats[:top_n]:
            size_diff_kb = stat.size_diff / 1024
            count_diff = stat.count_diff
            print(f"{str(stat):<60} {size_diff_kb:>15.2f}")
        
        print()
        
    def print_top_allocations(self, top_n: int = 20):
        """打印当前最大的内存分配"""
        if not self.snapshots:
            print("[MemoryTracker] 没有快照")
            return
        
        label, snapshot = self.snapshots[-1]
        
        print("=" * 80)
        print(f"当前内存分配 Top {top_n} ({label})")
        print("=" * 80)
        
        top_stats = snapshot.statistics('lineno')
        
        print(f"{'文件:行号':<60} {'大小(KB)':>15} {'数量':>10}")
        print("-" * 80)
        
        for stat in top_stats[:top_n]:
            size_kb = stat.size / 1024
            count = stat.count
            print(f"{str(stat):<60} {size_kb:>15.2f} {count:>10}")
        
        print()
        
    def print_traceback(self, top_n: int = 10):
        """打印带回溯的内存分配"""
        if not self.snapshots:
            print("[MemoryTracker] 没有快照")
            return
        
        label, snapshot = self.snapshots[-1]
        
        print("=" * 80)
        print(f"内存分配回溯 Top {top_n} ({label})")
        print("=" * 80)
        
        top_stats = snapshot.statistics('traceback')
        
        for index, stat in enumerate(top_stats[:top_n], 1):
            print(f"\n#{index}: {stat.size / 1024:.1f} KB ({stat.count} blocks)")
            for line in stat.traceback.format():
                print(f"  {line}")
        
        print()
        
    def get_current_memory(self) -> Tuple[int, int]:
        """获取当前内存使用"""
        current, peak = tracemalloc.get_traced_memory()
        return current, peak
    
    def print_current_memory(self):
        """打印当前内存使用"""
        current, peak = self.get_current_memory()
        print(f"当前内存: {current / 1024 / 1024:.2f} MB")
        print(f"峰值内存: {peak / 1024 / 1024:.2f} MB")
        
    def stop(self):
        """停止追踪"""
        tracemalloc.stop()
        print("[MemoryTracker] 内存追踪已停止")


def analyze_with_objgraph():
    """使用 objgraph 分析对象引用"""
    try:
        import objgraph
    except ImportError:
        print("错误: 需要安装 objgraph")
        print("安装命令: pip install objgraph")
        return
    
    print("=" * 80)
    print("对象引用分析 (objgraph)")
    print("=" * 80)
    
    # 1. 最常见的对象类型
    print("\n【最常见的对象类型 Top 20】")
    print("-" * 80)
    objgraph.show_most_common_types(limit=20)
    
    # 2. 增长最快的对象类型
    print("\n【对象增长分析】")
    print("-" * 80)
    print("提示: 需要先调用 objgraph.show_growth() 建立基线")
    print("然后运行一段时间后再次调用查看增长")
    objgraph.show_growth(limit=20)
    
    print()


def find_memory_leaks():
    """查找可能的内存泄漏"""
    print("=" * 80)
    print("内存泄漏检测")
    print("=" * 80)
    
    # 强制垃圾回收
    print("\n执行垃圾回收...")
    collected = gc.collect()
    print(f"回收了 {collected} 个对象")
    
    # 检查无法回收的对象
    print("\n【无法回收的对象】")
    print("-" * 80)
    garbage = gc.garbage
    if garbage:
        print(f"发现 {len(garbage)} 个无法回收的对象:")
        for i, obj in enumerate(garbage[:10]):
            print(f"  {i+1}. {type(obj).__name__}: {repr(obj)[:100]}")
    else:
        print("未发现无法回收的对象")
    
    # 检查引用循环
    print("\n【引用循环检测】")
    print("-" * 80)
    gc.set_debug(gc.DEBUG_SAVEALL)
    collected = gc.collect()
    
    if gc.garbage:
        print(f"发现 {len(gc.garbage)} 个循环引用对象")
        for i, obj in enumerate(gc.garbage[:10]):
            print(f"  {i+1}. {type(obj).__name__}")
            referrers = gc.get_referrers(obj)
            print(f"     被 {len(referrers)} 个对象引用")
    else:
        print("未发现循环引用")
    
    gc.set_debug(0)
    print()


def interactive_mode():
    """交互式内存分析模式"""
    tracker = MemoryTracker()
    tracker.start()
    tracker.take_snapshot("baseline")
    
    print("\n" + "=" * 80)
    print("交互式内存分析模式")
    print("=" * 80)
    print("\n可用命令:")
    print("  snapshot [label]  - 拍摄快照")
    print("  compare [idx1] [idx2] - 比较快照 (默认: 0 和 -1)")
    print("  top [n]          - 显示 Top N 内存分配 (默认: 20)")
    print("  traceback [n]    - 显示带回溯的 Top N (默认: 10)")
    print("  current          - 显示当前内存使用")
    print("  objgraph         - 使用 objgraph 分析")
    print("  leaks            - 检测内存泄漏")
    print("  gc               - 执行垃圾回收")
    print("  list             - 列出所有快照")
    print("  help             - 显示帮助")
    print("  quit             - 退出")
    print()
    
    while True:
        try:
            cmd = input(">>> ").strip().split()
            if not cmd:
                continue
            
            action = cmd[0].lower()
            
            if action == "quit" or action == "exit":
                break
            
            elif action == "snapshot":
                label = cmd[1] if len(cmd) > 1 else None
                tracker.take_snapshot(label)
            
            elif action == "compare":
                idx1 = int(cmd[1]) if len(cmd) > 1 else 0
                idx2 = int(cmd[2]) if len(cmd) > 2 else -1
                tracker.compare_snapshots(idx1, idx2)
            
            elif action == "top":
                n = int(cmd[1]) if len(cmd) > 1 else 20
                tracker.print_top_allocations(n)
            
            elif action == "traceback":
                n = int(cmd[1]) if len(cmd) > 1 else 10
                tracker.print_traceback(n)
            
            elif action == "current":
                tracker.print_current_memory()
            
            elif action == "objgraph":
                analyze_with_objgraph()
            
            elif action == "leaks":
                find_memory_leaks()
            
            elif action == "gc":
                print("执行垃圾回收...")
                collected = gc.collect()
                print(f"回收了 {collected} 个对象")
                tracker.print_current_memory()
            
            elif action == "list":
                print("\n快照列表:")
                for i, (label, _) in enumerate(tracker.snapshots):
                    print(f"  {i}: {label}")
                print()
            
            elif action == "help":
                print("\n可用命令:")
                print("  snapshot [label]  - 拍摄快照")
                print("  compare [idx1] [idx2] - 比较快照")
                print("  top [n]          - 显示 Top N 内存分配")
                print("  traceback [n]    - 显示带回溯的 Top N")
                print("  current          - 显示当前内存使用")
                print("  objgraph         - 使用 objgraph 分析")
                print("  leaks            - 检测内存泄漏")
                print("  gc               - 执行垃圾回收")
                print("  list             - 列出所有快照")
                print("  help             - 显示帮助")
                print("  quit             - 退出")
                print()
            
            else:
                print(f"未知命令: {action}")
                print("输入 'help' 查看可用命令")
        
        except KeyboardInterrupt:
            print("\n使用 'quit' 退出")
        except Exception as e:
            print(f"错误: {e}")
    
    tracker.stop()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="高级内存追踪工具")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互式模式")
    parser.add_argument("--objgraph", action="store_true", help="使用 objgraph 分析")
    parser.add_argument("--leaks", action="store_true", help="检测内存泄漏")
    
    args = parser.parse_args()
    
    if args.interactive:
        interactive_mode()
    elif args.objgraph:
        analyze_with_objgraph()
    elif args.leaks:
        find_memory_leaks()
    else:
        parser.print_help()
        print("\n示例用法:")
        print("  1. 交互式内存分析:")
        print("     python tools/advanced_memory_tracker.py --interactive")
        print()
        print("  2. 使用 objgraph 分析对象:")
        print("     python tools/advanced_memory_tracker.py --objgraph")
        print()
        print("  3. 检测内存泄漏:")
        print("     python tools/advanced_memory_tracker.py --leaks")
