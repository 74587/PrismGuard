"""
客户端使用示例 - 展示如何使用代理
"""
import json
import urllib.parse
from openai import OpenAI


def create_proxy_client(config: dict, upstream: str, api_key: str, proxy_host: str = "http://localhost:8000"):
    """创建配置了代理的 OpenAI 客户端"""
    cfg_str = json.dumps(config, separators=(',', ':'))
    cfg_enc = urllib.parse.quote(cfg_str, safe='')
    base_url = f"{proxy_host}/{cfg_enc}${upstream}"
    
    return OpenAI(api_key=api_key, base_url=base_url)


# 示例 1: 基础使用（带审核）
def example_basic():
    config = {
        "basic_moderation": {"enabled": True, "keywords_file": "configs/keywords.txt"},
        "smart_moderation": {"enabled": True, "profile": "default"},
        "format_transform": {"enabled": False}
    }
    
    client = create_proxy_client(
        config=config,
        upstream="https://api.openai.com/v1",
        api_key="sk-xxx"
    )
    
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "你好"}]
    )
    print(response.choices[0].message.content)


# 示例 2: 工具调用（OpenAI 格式）
def example_tools():
    config = {
        "basic_moderation": {"enabled": False},
        "smart_moderation": {"enabled": False},
        "format_transform": {"enabled": False}
    }
    
    client = create_proxy_client(
        config=config,
        upstream="https://api.openai.com/v1",
        api_key="sk-xxx"
    )
    
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "城市名称"}
                },
                "required": ["location"]
            }
        }
    }]
    
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "北京天气怎么样"}],
        tools=tools
    )
    print(response.choices[0].message)


# 示例 3: 格式转换（OpenAI -> Claude）
def example_format_transform():
    config = {
        "basic_moderation": {"enabled": False},
        "smart_moderation": {"enabled": False},
        "format_transform": {
            "enabled": True,
            "from": "openai_chat",
            "to": "claude_chat"
        }
    }
    
    # 使用 OpenAI SDK，但实际会转换为 Claude 格式发送
    client = create_proxy_client(
        config=config,
        upstream="https://api.anthropic.com/v1",
        api_key="sk-ant-xxx"
    )
    
    response = client.chat.completions.create(
        model="claude-3-opus-20240229",
        messages=[{"role": "user", "content": "你好"}]
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    print("请根据实际情况修改 API Key 后运行示例")