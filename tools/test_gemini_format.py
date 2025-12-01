"""
测试 Gemini Chat 格式的识别和转换
"""
import json
from ai_proxy.transform.formats.parser import detect_and_parse, get_parser
from ai_proxy.transform.formats.gemini_chat import can_parse_gemini_chat


def test_gemini_format_detection():
    """测试 Gemini 格式识别"""
    print("\n" + "=" * 60)
    print("测试 Gemini 格式识别")
    print("=" * 60)
    
    # 测试用例 1: 典型的 Gemini 请求
    gemini_body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "你好"}
                ]
            }
        ]
    }
    
    # 测试不同的路径
    test_cases = [
        ("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent", True, "Gemini API 端点"),
        ("/v1beta/models/gemini-2.5-flash:streamGenerateContent", True, "Gemini 流式端点"),
        ("/chat/completions", False, "OpenAI 端点"),
        ("/v1/messages", False, "Claude 端点"),
    ]
    
    for path, should_detect, desc in test_cases:
        result = can_parse_gemini_chat(path, {}, gemini_body)
        status = "✅" if result == should_detect else "❌"
        print(f"{status} {desc}: {path}")
        print(f"   检测结果: {result}, 期望: {should_detect}")


def test_gemini_to_internal():
    """测试 Gemini 格式到内部格式的转换"""
    print("\n" + "=" * 60)
    print("测试 Gemini -> 内部格式转换")
    print("=" * 60)
    
    gemini_body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "你好，请介绍一下你自己"}
                ]
            },
            {
                "role": "model",
                "parts": [
                    {"text": "你好！我是一个 AI 助手。"}
                ]
            },
            {
                "role": "user",
                "parts": [
                    {"text": "很高兴认识你"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024
        },
        "safetySettings": [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_MEDIUM_AND_ABOVE"
            }
        ]
    }
    
    path = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    headers = {}
    
    # 使用 detect_and_parse
    format_name, internal_req, error = detect_and_parse(
        "auto", path, headers, gemini_body, strict_parse=False, disable_tools=False
    )
    
    print(f"检测到的格式: {format_name}")
    print(f"错误信息: {error}")
    
    if internal_req:
        print(f"\n转换成功！")
        print(f"消息数量: {len(internal_req.messages)}")
        for i, msg in enumerate(internal_req.messages):
            print(f"\n消息 {i + 1}:")
            print(f"  角色: {msg.role}")
            for j, block in enumerate(msg.content):
                print(f"  内容块 {j + 1}: {block.type}")
                if block.text:
                    print(f"    文本: {block.text[:50]}...")
        
        print(f"\n额外配置:")
        print(f"  generationConfig: {internal_req.extra.get('generationConfig')}")
        print(f"  safetySettings: {internal_req.extra.get('safetySettings')}")


def test_internal_to_gemini():
    """测试内部格式到 Gemini 格式的转换"""
    print("\n" + "=" * 60)
    print("测试 内部格式 -> Gemini 转换")
    print("=" * 60)
    
    from ai_proxy.transform.formats.internal_models import (
        InternalChatRequest,
        InternalMessage,
        InternalContentBlock
    )
    
    # 创建内部格式请求
    internal_req = InternalChatRequest(
        messages=[
            InternalMessage(
                role="system",
                content=[InternalContentBlock(type="text", text="你是一个友好的助手")]
            ),
            InternalMessage(
                role="user",
                content=[InternalContentBlock(type="text", text="你好")]
            ),
            InternalMessage(
                role="assistant",
                content=[InternalContentBlock(type="text", text="你好！很高兴为你服务。")]
            )
        ],
        model="gemini-2.5-flash",
        stream=False,
        tools=[],
        extra={
            "generationConfig": {
                "temperature": 0.7
            }
        }
    )
    
    # 转换为 Gemini 格式
    parser = get_parser("gemini_chat")
    if parser:
        gemini_body = parser.to_format(internal_req)
        
        print("转换后的 Gemini 格式:")
        print(json.dumps(gemini_body, indent=2, ensure_ascii=False))
        
        # 验证关键字段
        assert "contents" in gemini_body, "缺少 contents 字段"
        assert "systemInstruction" in gemini_body, "缺少 systemInstruction 字段"
        assert len(gemini_body["contents"]) == 2, f"消息数量错误: {len(gemini_body['contents'])}"
        
        # 验证 role 转换
        assert gemini_body["contents"][0]["role"] == "user", "user 角色错误"
        assert gemini_body["contents"][1]["role"] == "model", "model 角色错误"
        
        print("\n✅ 所有验证通过！")
    else:
        print("❌ 找不到 gemini_chat 解析器")


def test_format_priority():
    """测试格式检测的优先级"""
    print("\n" + "=" * 60)
    print("测试格式检测优先级")
    print("=" * 60)
    
    # 创建一个可能被误判的请求体
    ambiguous_body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "测试"}]
            }
        ]
    }
    
    test_cases = [
        {
            "path": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            "expected": "gemini_chat",
            "desc": "明确的 Gemini 端点"
        },
        {
            "path": "/chat/completions",
            "expected": None,  # 应该无法识别，因为没有 messages 字段
            "desc": "OpenAI 端点但 Gemini body"
        },
        {
            "path": "/v1/messages",
            "expected": None,  # 应该无法识别，因为没有 messages 字段
            "desc": "Claude 端点但 Gemini body"
        }
    ]
    
    for case in test_cases:
        format_name, _, _ = detect_and_parse(
            "auto", case["path"], {}, ambiguous_body, strict_parse=False
        )
        
        status = "✅" if format_name == case["expected"] else "❌"
        print(f"{status} {case['desc']}")
        print(f"   检测结果: {format_name}, 期望: {case['expected']}")


def test_gemini_response_conversion():
    """测试 Gemini 响应格式的转换"""
    print("\n" + "=" * 60)
    print("测试 Gemini 响应格式转换")
    print("=" * 60)
    
    # 模拟 Gemini 响应
    gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "你好！我是 Gemini，很高兴为你服务。"}
                    ],
                    "role": "model"
                },
                "finishReason": "STOP"
            }
        ],
        "modelVersion": "gemini-2.5-flash",
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 20,
            "totalTokenCount": 30
        }
    }
    
    parser = get_parser("gemini_chat")
    if parser:
        # Gemini 响应 -> 内部格式
        internal_resp = parser.resp_to_internal(gemini_response)
        
        print("内部格式响应:")
        print(f"  ID: {internal_resp.id}")
        print(f"  模型: {internal_resp.model}")
        print(f"  完成原因: {internal_resp.finish_reason}")
        print(f"  消息数量: {len(internal_resp.messages)}")
        
        if internal_resp.messages:
            msg = internal_resp.messages[0]
            print(f"  角色: {msg.role}")
            for block in msg.content:
                if block.text:
                    print(f"  文本: {block.text}")
        
        # 内部格式 -> Gemini 响应
        converted_back = parser.internal_to_resp(internal_resp)
        
        print("\n转换回 Gemini 格式:")
        print(json.dumps(converted_back, indent=2, ensure_ascii=False))
        
        # 验证关键字段
        assert "candidates" in converted_back, "缺少 candidates 字段"
        assert converted_back["candidates"][0]["content"]["role"] == "model", "角色应为 model"
        
        print("\n✅ 响应转换测试通过！")
    else:
        print("❌ 找不到 gemini_chat 解析器")


if __name__ == "__main__":
    try:
        test_gemini_format_detection()
        test_gemini_to_internal()
        test_internal_to_gemini()
        test_format_priority()
        test_gemini_response_conversion()
        
        print("\n" + "=" * 60)
        print("✅ 所有测试完成！")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()