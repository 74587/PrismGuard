"""
测试格式解析器 - 验证新添加的 Claude Code 和 OpenAI Codex 解析器
"""
import json
from ai_proxy.transform.formats.parser import detect_and_parse, get_parser


def test_claude_code_format():
    """测试 Claude Code 格式解析"""
    print("\n=== 测试 Claude Code 格式 ===")
    
    # Claude Code 格式示例
    body = {
        "prompt": "Analyze this code",
        "options": {
            "model": "claude-sonnet-4-5",
            "workingDirectory": "/path/to/project",
            "systemPrompt": "You are a code reviewer"
        }
    }
    
    path = "/api/query"
    headers = {}
    
    format_name, internal = detect_and_parse("auto", path, headers, body)
    
    if format_name:
        print(f"✅ 检测到格式: {format_name}")
        print(f"   模型: {internal.model}")
        print(f"   消息数: {len(internal.messages)}")
        print(f"   第一条消息: {internal.messages[0].role} - {internal.messages[0].content[0].text[:50]}...")
    else:
        print("❌ 未能识别格式")
    
    return format_name == "claude_code"


def test_openai_codex_format():
    """测试 OpenAI Codex/Completions 格式解析"""
    print("\n=== 测试 OpenAI Codex 格式 ===")
    
    # OpenAI Completions API 格式示例
    body = {
        "model": "text-davinci-003",
        "prompt": "Write a Python function to calculate fibonacci",
        "max_tokens": 100,
        "temperature": 0.7
    }
    
    path = "/v1/completions"
    headers = {}
    
    format_name, internal = detect_and_parse("auto", path, headers, body)
    
    if format_name:
        print(f"✅ 检测到格式: {format_name}")
        print(f"   模型: {internal.model}")
        print(f"   消息数: {len(internal.messages)}")
        print(f"   第一条消息: {internal.messages[0].role} - {internal.messages[0].content[0].text[:50]}...")
    else:
        print("❌ 未能识别格式")
    
    return format_name == "openai_codex"


def test_claude_chat_exclusion():
    """测试 Claude Chat 格式排斥 Claude Code"""
    print("\n=== 测试 Claude Chat 排斥 Claude Code ===")
    
    # Claude Code 格式不应被 Claude Chat 识别
    body = {
        "prompt": "Test prompt",
        "options": {"model": "claude-sonnet-4-5"}
    }
    
    path = "/api/query"
    headers = {}
    
    parser = get_parser("claude_chat")
    can_parse = parser.can_parse(path, headers, body)
    
    if not can_parse:
        print("✅ Claude Chat 正确排斥了 Claude Code 格式")
    else:
        print("❌ Claude Chat 错误识别了 Claude Code 格式")
    
    return not can_parse


def test_openai_chat_exclusion():
    """测试 OpenAI Chat 格式排斥 OpenAI Codex"""
    print("\n=== 测试 OpenAI Chat 排斥 OpenAI Codex ===")
    
    # OpenAI Codex 格式不应被 OpenAI Chat 识别
    body = {
        "model": "text-davinci-003",
        "prompt": "Test prompt",
        "max_tokens": 100
    }
    
    path = "/v1/completions"
    headers = {}
    
    parser = get_parser("openai_chat")
    can_parse = parser.can_parse(path, headers, body)
    
    if not can_parse:
        print("✅ OpenAI Chat 正确排斥了 OpenAI Codex 格式")
    else:
        print("❌ OpenAI Chat 错误识别了 OpenAI Codex 格式")
    
    return not can_parse


def test_format_conversion():
    """测试格式转换"""
    print("\n=== 测试格式转换 ===")
    
    # 测试 Claude Code -> OpenAI Chat 转换
    print("\n1. Claude Code -> OpenAI Chat")
    
    claude_code_body = {
        "prompt": "Write a hello world program",
        "options": {
            "model": "claude-sonnet-4-5",
            "systemPrompt": "You are a helpful assistant"
        }
    }
    
    # 解析 Claude Code 格式
    claude_parser = get_parser("claude_code")
    internal = claude_parser.from_format(claude_code_body)
    
    # 转换为 OpenAI Chat 格式
    openai_parser = get_parser("openai_chat")
    openai_body = openai_parser.to_format(internal)
    
    print(f"   原始格式: Claude Code")
    print(f"   转换后: OpenAI Chat")
    print(f"   消息数: {len(openai_body.get('messages', []))}")
    print(f"   模型: {openai_body.get('model')}")
    
    # 测试 OpenAI Codex -> Claude Chat 转换
    print("\n2. OpenAI Codex -> Claude Chat")
    
    codex_body = {
        "model": "text-davinci-003",
        "prompt": "Explain quantum computing",
        "max_tokens": 200
    }
    
    # 解析 OpenAI Codex 格式
    codex_parser = get_parser("openai_codex")
    internal = codex_parser.from_format(codex_body)
    
    # 转换为 Claude Chat 格式
    claude_chat_parser = get_parser("claude_chat")
    claude_body = claude_chat_parser.to_format(internal)
    
    print(f"   原始格式: OpenAI Codex")
    print(f"   转换后: Claude Chat")
    print(f"   消息数: {len(claude_body.get('messages', []))}")
    print(f"   模型: {claude_body.get('model')}")
    
    return True


def test_auto_detection():
    """测试自动检测功能"""
    print("\n=== 测试自动格式检测 ===")
    
    test_cases = [
        {
            "name": "OpenAI Chat",
            "path": "/v1/chat/completions",
            "headers": {},
            "body": {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]},
            "expected": "openai_chat"
        },
        {
            "name": "Claude Chat",
            "path": "/v1/messages",
            "headers": {"anthropic-version": "2023-06-01"},
            "body": {"model": "claude-3-5-sonnet-20241022", "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]},
            "expected": "claude_chat"
        },
        {
            "name": "Claude Code",
            "path": "/api/query",
            "headers": {},
            "body": {"prompt": "Test", "options": {"model": "claude-sonnet-4-5"}},
            "expected": "claude_code"
        },
        {
            "name": "OpenAI Codex",
            "path": "/v1/completions",
            "headers": {},
            "body": {"model": "text-davinci-003", "prompt": "Test", "max_tokens": 100},
            "expected": "openai_codex"
        }
    ]
    
    results = []
    for case in test_cases:
        format_name, internal = detect_and_parse("auto", case["path"], case["headers"], case["body"])
        success = format_name == case["expected"]
        results.append(success)
        
        status = "✅" if success else "❌"
        print(f"{status} {case['name']}: 期望 {case['expected']}, 实际 {format_name}")
    
    return all(results)


def main():
    """运行所有测试"""
    print("=" * 60)
    print("格式解析器测试工具")
    print("=" * 60)
    
    tests = [
        ("Claude Code 格式解析", test_claude_code_format),
        ("OpenAI Codex 格式解析", test_openai_codex_format),
        ("Claude Chat 排斥测试", test_claude_chat_exclusion),
        ("OpenAI Chat 排斥测试", test_openai_chat_exclusion),
        ("格式转换测试", test_format_conversion),
        ("自动检测测试", test_auto_detection)
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} 失败: {e}")
            results.append((name, False))
    
    # 打印总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status}: {name}")
    
    print(f"\n总计: {passed}/{total} 测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print("\n⚠️ 部分测试失败，请检查实现")
        return 1


if __name__ == "__main__":
    exit(main())