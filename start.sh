#!/bin/bash

# GuardianBridge 启动脚本（使用 uv）

# 检查 uv
if ! command -v uv &> /dev/null; then
    echo "错误: 未找到 uv，请先安装: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# 检查配置文件
if [ ! -f ".env" ]; then
    echo "警告: 未找到 .env 文件，请复制 .env.example 并配置"
    exit 1
fi

# 使用 uv 运行（自动管理虚拟环境和依赖）
echo "启动 GuardianBridge..."
uv run uvicorn ai_proxy.app:app --host 0.0.0.0 --port 8000 --reload