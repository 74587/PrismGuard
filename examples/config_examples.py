"""
配置示例 - 展示各种使用场景
"""
import json
import urllib.parse


def create_proxy_url(config: dict, upstream: str, proxy_host: str = "http://localhost:8000") -> str:
    """创建代理 URL"""
    cfg_str = json.dumps(config, separators=(',', ':'))
    cfg_enc = urllib.parse.quote(cfg_str, safe='')
    return f"{proxy_host}/{cfg_enc}${upstream}"


# 示例 1: 仅基础审核
config_basic_only = {
    "basic_moderation": {
        "enabled": True,
        "keywords_file": "configs/keywords.txt"
    },
    "smart_moderation": {
        "enabled": False
    },
    "format_transform": {
        "enabled": False
    }
}

# 示例 2: 基础 + 智能审核
config_with_smart = {
    "basic_moderation": {
        "enabled": True,
        "keywords_file": "configs/keywords.txt"
    },
    "smart_moderation": {
        "enabled": True,
        "profile": "default"
    },
    "format_transform": {
        "enabled": False
    }
}

# 示例 3: OpenAI -> Claude 转换（支持工具调用）
config_openai_to_claude = {
    "basic_moderation": {
        "enabled": False
    },
    "smart_moderation": {
        "enabled": False
    },
    "format_transform": {
        "enabled": True,
        "from": "openai_chat",
        "to": "claude_chat",
        "stream": "auto"
    }
}

# 示例 4: 多来源自动检测
config_auto_detect = {
    "basic_moderation": {
        "enabled": True,
        "keywords_file": "configs/keywords.txt"
    },
    "smart_moderation": {
        "enabled": True,
        "profile": "default"
    },
    "format_transform": {
        "enabled": True,
        "from": "auto",  # 自动检测所有支持的格式
        "to": "openai_chat",
        "stream": "auto"
    }
}

# 示例 5: 指定多个来源格式
config_multi_source = {
    "basic_moderation": {
        "enabled": True,
        "keywords_file": "configs/keywords.txt"
    },
    "smart_moderation": {
        "enabled": False
    },
    "format_transform": {
        "enabled": True,
        "from": ["openai_chat", "claude_chat"],  # 只支持这两种
        "to": "openai_chat",
        "stream": "auto"
    }
}

# 示例 6: 完整配置（所有功能开启）
config_full = {
    "basic_moderation": {
        "enabled": True,
        "keywords_file": "configs/keywords.txt",
        "error_code": "BASIC_MODERATION_BLOCKED"
    },
    "smart_moderation": {
        "enabled": True,
        "profile": "default"
    },
    "format_transform": {
        "enabled": True,
        "from": "auto",
        "to": "openai_chat",
        "stream": "auto",
        "detect": {
            "by_path": True,
            "by_header": True,
            "by_body": True
        }
    }
}


if __name__ == "__main__":
    # 生成示例 URL
    print("=" * 60)
    print("代理 URL 示例")
    print("=" * 60)
    
    print("\n1. 仅基础审核:")
    url1 = create_proxy_url(config_basic_only, "https://api.openai.com/v1")
    print(f"   {url1[:100]}...")
    
    print("\n2. 基础 + 智能审核:")
    url2 = create_proxy_url(config_with_smart, "https://api.openai.com/v1")
    print(f"   {url2[:100]}...")
    
    print("\n3. OpenAI -> Claude 转换:")
    url3 = create_proxy_url(config_openai_to_claude, "https://api.anthropic.com/v1")
    print(f"   {url3[:100]}...")
    
    print("\n4. 自动检测格式:")
    url4 = create_proxy_url(config_auto_detect, "https://api.openai.com/v1")
    print(f"   {url4[:100]}...")
    
    print("\n5. 完整配置:")
    url5 = create_proxy_url(config_full, "https://api.openai.com/v1")
    print(f"   {url5[:100]}...")