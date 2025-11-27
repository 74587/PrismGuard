#!/bin/bash

# GuardianBridge 启动脚本（使用 uv）

# 查找 uv 可执行文件
UV_CMD=""
if command -v uv &> /dev/null; then
    UV_CMD="uv"
elif [ -f "$HOME/.local/bin/uv" ]; then
    UV_CMD="$HOME/.local/bin/uv"
elif [ -f "/root/.local/bin/uv" ]; then
    UV_CMD="/root/.local/bin/uv"
else
    echo "错误: 未找到 uv，请先安装:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "使用 uv: $UV_CMD"

# 检查配置文件
if [ ! -f ".env" ]; then
    echo "警告: 未找到 .env 文件，请复制 .env.example 并配置"
    exit 1
fi

# 使用 uv 运行（自动管理虚拟环境和依赖）
# 注意：supervisor 需要进程在前台运行，所以不使用 --reload
echo "启动 GuardianBridge..."
exec $UV_CMD run uvicorn ai_proxy.app:app --host 0.0.0.0 --port 8000