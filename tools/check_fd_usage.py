#!/usr/bin/env python3
"""
检查进程的文件描述符使用情况
"""
import os
import sys
import psutil
import subprocess
from pathlib import Path


def find_uvicorn_process():
    """查找 uvicorn 进程"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and 'uvicorn' in ' '.join(cmdline) and 'ai_proxy.app:app' in ' '.join(cmdline):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def get_fd_count(pid):
    """获取进程的文件描述符数量"""
    try:
        fd_dir = Path(f"/proc/{pid}/fd")
        if fd_dir.exists():
            return len(list(fd_dir.iterdir()))
    except (PermissionError, FileNotFoundError):
        pass
    
    # 备用方法：使用 psutil
    try:
        proc = psutil.Process(pid)
        return proc.num_fds()
    except:
        return None


def get_limits(pid):
    """获取进程的资源限制"""
    try:
        with open(f"/proc/{pid}/limits", 'r') as f:
            for line in f:
                if 'open files' in line.lower():
                    parts = line.split()
                    soft = parts[3]
                    hard = parts[4]
                    return soft, hard
    except:
        pass
    return None, None


def get_fd_types(pid):
    """获取文件描述符类型分布"""
    try:
        result = subprocess.run(
            ['lsof', '-p', str(pid)],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            types = {}
            for line in result.stdout.split('\n')[1:]:  # 跳过标题行
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 5:
                        fd_type = parts[4]
                        types[fd_type] = types.get(fd_type, 0) + 1
            return types
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    
    # 备用方法：直接读取 /proc/pid/fd
    try:
        fd_dir = Path(f"/proc/{pid}/fd")
        types = {}
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(str(fd))
                if 'socket:' in target:
                    fd_type = 'socket'
                elif 'pipe:' in target:
                    fd_type = 'pipe'
                elif target.startswith('/'):
                    fd_type = 'file'
                else:
                    fd_type = 'other'
                types[fd_type] = types.get(fd_type, 0) + 1
            except:
                pass
        return types
    except:
        pass
    
    return {}


def main():
    print("=== 查找 uvicorn 进程 ===")
    proc = find_uvicorn_process()
    
    if not proc:
        print("❌ 未找到 uvicorn 进程")
        sys.exit(1)
    
    pid = proc.pid
    print(f"✅ 找到进程 PID: {pid}")
    print(f"   命令: {' '.join(proc.cmdline())}")
    
    # 进程信息
    print("\n=== 进程信息 ===")
    try:
        mem_info = proc.memory_info()
        print(f"内存使用 (RSS): {mem_info.rss / 1024 / 1024:.2f} MB")
        print(f"虚拟内存 (VMS): {mem_info.vms / 1024 / 1024:.2f} MB")
        print(f"CPU 使用率: {proc.cpu_percent(interval=1):.1f}%")
        print(f"线程数: {proc.num_threads()}")
        print(f"运行时间: {proc.create_time()}")
    except:
        pass
    
    # 文件描述符使用情况
    print("\n=== 文件描述符使用情况 ===")
    fd_count = get_fd_count(pid)
    if fd_count is not None:
        print(f"当前使用: {fd_count}")
    else:
        print("❌ 无法获取文件描述符数量")
        sys.exit(1)
    
    soft_limit, hard_limit = get_limits(pid)
    if soft_limit:
        print(f"软限制: {soft_limit}")
        print(f"硬限制: {hard_limit}")
        
        # 计算使用率
        if soft_limit != 'unlimited':
            try:
                soft_limit_num = int(soft_limit)
                usage_percent = (fd_count / soft_limit_num) * 100
                print(f"使用率: {usage_percent:.2f}%")
                
                if usage_percent > 90:
                    print("🔴 危险：使用率超过 90%！")
                elif usage_percent > 80:
                    print("⚠️  警告：使用率超过 80%")
                elif usage_percent > 50:
                    print("⚡ 注意：使用率超过 50%")
                else:
                    print("✅ 使用率正常")
            except ValueError:
                pass
    
    # 文件描述符类型分布
    print("\n=== 文件描述符类型分布 ===")
    fd_types = get_fd_types(pid)
    if fd_types:
        # 按数量排序
        sorted_types = sorted(fd_types.items(), key=lambda x: x[1], reverse=True)
        for fd_type, count in sorted_types:
            print(f"  {fd_type:<15} {count:>6}")
    else:
        print("  (无法获取类型分布)")
    
    # 系统级别信息
    print("\n=== 系统级别限制 ===")
    try:
        with open('/proc/sys/fs/file-max', 'r') as f:
            file_max = f.read().strip()
            print(f"系统最大文件描述符: {file_max}")
    except:
        pass
    
    try:
        with open('/proc/sys/fs/file-nr', 'r') as f:
            parts = f.read().strip().split()
            print(f"系统当前使用: {parts[0]}")
            print(f"系统已分配: {parts[1]}")
            print(f"系统最大值: {parts[2]}")
    except:
        pass
    
    try:
        with open('/proc/sys/fs/nr_open', 'r') as f:
            nr_open = f.read().strip()
            print(f"单进程最大打开文件数: {nr_open}")
    except:
        pass
    
    # 建议
    print("\n=== 建议 ===")
    if fd_count < 100:
        print("✅ 文件描述符使用很少，系统运行正常")
    elif fd_count < 1000:
        print("✅ 文件描述符使用正常")
    elif fd_count < 10000:
        print("⚡ 文件描述符使用较多，建议监控")
    else:
        print("⚠️  文件描述符使用很多，建议检查是否有泄漏")
    
    if soft_limit and soft_limit != 'unlimited':
        try:
            soft_limit_num = int(soft_limit)
            recommended = fd_count * 2
            if recommended > soft_limit_num:
                print(f"💡 建议将 ulimit -n 提高到至少 {recommended}")
        except:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
