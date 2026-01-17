#!/bin/bash
# 检查进程的文件描述符使用情况

# 查找进程
PID=$(ps aux | grep "uvicorn ai_proxy.app:app" | grep -v grep | awk '{print $2}' | head -1)

if [ -z "$PID" ]; then
    echo "❌ 未找到 uvicorn 进程"
    exit 1
fi

echo "=== 进程信息 ==="
echo "PID: $PID"
ps aux | grep $PID | grep -v grep
echo ""

echo "=== 文件描述符使用情况 ==="
# 当前使用的文件描述符数量
FD_COUNT=$(ls /proc/$PID/fd 2>/dev/null | wc -l)
echo "当前使用: $FD_COUNT"

# 获取限制
SOFT_LIMIT=$(cat /proc/$PID/limits | grep "open files" | awk '{print $4}')
HARD_LIMIT=$(cat /proc/$PID/limits | grep "open files" | awk '{print $5}')
echo "软限制: $SOFT_LIMIT"
echo "硬限制: $HARD_LIMIT"

# 计算使用率
if [ "$SOFT_LIMIT" != "unlimited" ]; then
    USAGE_PERCENT=$(echo "scale=2; $FD_COUNT * 100 / $SOFT_LIMIT" | bc)
    echo "使用率: ${USAGE_PERCENT}%"
    
    if (( $(echo "$USAGE_PERCENT > 80" | bc -l) )); then
        echo "⚠️  警告：使用率超过 80%"
    elif (( $(echo "$USAGE_PERCENT > 90" | bc -l) )); then
        echo "🔴 危险：使用率超过 90%"
    else
        echo "✅ 使用率正常"
    fi
fi

echo ""
echo "=== 文件描述符类型分布 ==="
if command -v lsof &> /dev/null; then
    lsof -p $PID 2>/dev/null | awk 'NR>1 {print $5}' | sort | uniq -c | sort -rn | head -10
else
    echo "需要安装 lsof: apt-get install lsof"
    echo ""
    echo "使用备用方法统计:"
    ls -l /proc/$PID/fd 2>/dev/null | awk '{print $11}' | grep -E "socket|pipe|/dev" | cut -d'[' -f1 | sort | uniq -c | sort -rn
fi

echo ""
echo "=== 完整限制信息 ==="
cat /proc/$PID/limits

echo ""
echo "=== 系统级别限制 ==="
echo "系统最大文件描述符: $(cat /proc/sys/fs/file-max)"
echo "系统当前使用: $(cat /proc/sys/fs/file-nr | awk '{print $1}')"
echo "系统进程最大打开文件数: $(cat /proc/sys/fs/nr_open)"
