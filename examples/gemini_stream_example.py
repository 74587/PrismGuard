"""
Gemini 流式客户端示例 - 请求中间件
演示如何使用 Google AI Python SDK 通过中间件进行流式请求
"""
import os
import json
import google.generativeai as genai

def main():
    """使用 Gemini SDK 流式请求中间件"""
    
    # 1. 配置中间件 URL 和上游
    # 上游使用 Gemini API
    upstream = "http://100.64.0.101:20010/proxy/gemini1"
    
    # 中间件配置：启用所有格式转换为 gemini_chat + 启用 delay_stream_header
    config = {
        "basic_moderation": {
            "enabled": False
        },
        "smart_moderation": {
            "enabled": False
        },
        "format_transform": {
            "enabled": True,
            "from": "gemini_chat",  # 客户端发送 gemini 格式
            "to": "gemini_chat",    # 上游也是 gemini 格式（不转换，只检查）
            "delay_stream_header": True  # 启用流式响应头延迟
        }
    }
    
    # 将配置编码到 URL 中
    import urllib.parse
    config_str = json.dumps(config, separators=(',', ':'))
    print(config_str)
    config_encoded = urllib.parse.quote(config_str, safe='')
    
    # 中间件地址（假设运行在本地 8000 端口）
    middleware_base = "http://localhost:8000"
    
    # 构建完整的代理 URL
    # 格式: {middleware}/{config}${upstream}
    proxy_url = f"{middleware_base}/{config_encoded}${upstream}"
    
    print("=" * 60)
    print("Gemini 流式客户端示例")
    print("=" * 60)
    print(f"中间件地址: {middleware_base}")
    print(f"上游地址: {upstream}")
    print(f"配置: {json.dumps(config, indent=2)}")
    print("=" * 60)
    
    # 2. 配置 Gemini SDK
    # 从环境变量获取 API Key
    api_key = os.getenv("GEMINI_API_KEY","sk-xO8U7_xJTKs7ywjDqsQ1Tk_skWP1mriJQ2TT8GmHnxcElGyY")
    if not api_key:
        print("错误: 请设置环境变量 GEMINI_API_KEY")
        return
    
    # 配置 Gemini SDK 使用我们的中间件
    genai.configure(
        api_key=api_key,
        transport="rest",  # 使用 REST 传输
        client_options={
            "api_endpoint": proxy_url  # 指向中间件
        }
    )
    
    # 3. 创建模型实例
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    # 4. 发送流式请求
    print("\n发送流式请求...")
    print("-" * 60)
    
    try:
        # 使用 generate_content 的 stream=True 参数
        response = model.generate_content(
            "写一首关于人工智能的短诗",
            stream=True
        )
        
        print("收到响应流:\n")
        
        # 逐块接收并打印
        for chunk in response:
            if chunk.text:
                print(chunk.text, end='', flush=True)
        
        print("\n")
        print("-" * 60)
        print("流式请求完成!")
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


def test_non_stream():
    """测试非流式请求（用于对比）"""
    
    upstream = "https://generativelanguage.googleapis.com"
    
    config = {
        "basic_moderation": {"enabled": False},
        "smart_moderation": {"enabled": False},
        "format_transform": {
            "enabled": True,
            "from": "gemini_chat",
            "to": "gemini_chat",
            "delay_stream_header": True  # 非流式也可以启用内容检查
        }
    }
    
    import urllib.parse
    config_str = json.dumps(config, separators=(',', ':'))
    print(config_str)
    config_encoded = urllib.parse.quote(config_str, safe='')
    
    middleware_base = "http://localhost:8000"
    proxy_url = f"{middleware_base}/{config_encoded}${upstream}"
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("错误: 请设置环境变量 GEMINI_API_KEY")
        return
    
    genai.configure(
        api_key=api_key,
        transport="rest",
        client_options={"api_endpoint": proxy_url}
    )
    
    model = genai.GenerativeModel('gemini-2.0-flash-exp')
    
    print("\n" + "=" * 60)
    print("测试非流式请求")
    print("=" * 60)
    
    try:
        response = model.generate_content("你好，请介绍一下你自己")
        print(f"响应: {response.text}")
        print("=" * 60)
        
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


def test_direct_upstream():
    """直接请求上游（不通过中间件）"""
    
    upstream = "http://100.64.0.101:20010/proxy/gemini3"
    
    api_key = os.getenv("GEMINI_API_KEY","sk-l1um8F0QH3iuSftGTMNCo9h9WmnBSYkbVdHPpZdj-pN0Y8uM")
    if not api_key:
        print("错误: 请设置环境变量 GEMINI_API_KEY")
        return
    
    print("\n" + "=" * 60)
    print("直接请求上游（对比测试）")
    print("=" * 60)
    print(f"上游地址: {upstream}")
    print("=" * 60)
    
    # 配置 Gemini SDK 直接指向上游
    genai.configure(
        api_key=api_key,
        transport="rest",
        client_options={"api_endpoint": upstream}
    )
    
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    print("\n发送流式请求...")
    print("-" * 60)
    
    try:
        response = model.generate_content(
            "写一首关于人工智能的短诗",
            stream=True
        )
        
        print("收到响应流:\n")
        
        for chunk in response:
            if chunk.text:
                print(chunk.text, end='', flush=True)
        
        print("\n")
        print("-" * 60)
        print("流式请求完成!")
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import sys
    
    print("\n选择测试模式:")
    print("1. 通过中间件请求（流式）")
    print("2. 直接请求上游（流式）")
    print("3. 对比测试（先中间件，后直连）")
    print("4. 非流式请求")
    
    choice = input("\n请输入选项 (1-4，默认 1): ").strip() or "1"
    
    if choice == "1":
        main()
    elif choice == "2":
        test_direct_upstream()
    elif choice == "3":
        print("\n【测试 1/2】通过中间件请求")
        main()
        print("\n" + "="*60)
        input("按回车继续第二个测试...")
        print("\n【测试 2/2】直接请求上游")
        test_direct_upstream()
    elif choice == "4":
        test_non_stream()
    else:
        print("无效选项")
        sys.exit(1)