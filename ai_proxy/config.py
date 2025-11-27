"""
全局配置文件
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    
    # 日志配置
    LOG_LEVEL: str = "INFO"
    ACCESS_LOG_FILE: str = "logs/access.log"
    MODERATION_LOG_FILE: str = "logs/moderation.log"
    TRAINING_LOG_FILE: str = "logs/training.log"
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "allow"  # 允许额外字段（如各种审核API key）
    }


settings = Settings()