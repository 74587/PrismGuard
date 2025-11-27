"""
GuardianBridge (守桥) 主入口
FastAPI 应用启动文件
"""
from fastapi import FastAPI
from ai_proxy.proxy.router import router
from ai_proxy.config import settings

app = FastAPI(
    title="GuardianBridge",
    description="高级 AI API 中间件 - 智能审核 · 格式转换 · 透明代理",
    version="1.0.0"
)

app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "ai_proxy.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )