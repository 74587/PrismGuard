"""
Gemini Chat 格式转换 (AI Studio 格式)
"""
import json
from typing import Dict, Any
from ai_proxy.transform.formats.internal_models import (
    InternalChatRequest,
    InternalChatResponse,
    InternalMessage,
    InternalContentBlock,
    InternalTool,
    InternalToolCall,
    InternalToolResult
)


def can_parse_gemini_chat(path: str, headers: Dict[str, str], body: Dict[str, Any]) -> bool:
    """
    判断是否为 Gemini Chat 格式
    
    优先级：
    1. URL 路径判断（最可靠）
    2. 请求体结构判断（次要）
    
    Gemini API 端点特征：
    - generativelanguage.googleapis.com
    - /v1beta/models/{model}:generateContent
    - /v1beta/models/{model}:streamGenerateContent
    """
    # 1. 优先通过 URL 路径判断 - 这是最可靠的方式
    if "generativelanguage.googleapis.com" in path:
        return True
    
    if "generateContent" in path or "streamGenerateContent" in path:
        return True
    
    # AI Studio 的端点
    if "aistudio.google.com" in path or "/v1beta/models/" in path:
        return True
    
    # 2. 如果路径不明确，通过 body 结构判断
    # 必须同时满足：有 contents 数组，且第一个元素有 parts 字段
    if "contents" not in body:
        return False
    
    contents = body.get("contents")
    if not isinstance(contents, list) or not contents:
        return False
    
    first_content = contents[0]
    if not isinstance(first_content, dict):
        return False
    
    # Gemini 的关键特征：必须有 "parts" 字段
    if "parts" not in first_content:
        return False
    
    parts = first_content.get("parts")
    if not isinstance(parts, list):
        return False
    
    # 检查 role 是否为 Gemini 特有的值
    role = first_content.get("role", "")
    if role == "model":  # "model" 是 Gemini 特有的，OpenAI 和 Claude 都用 "assistant"
        return True
    
    # 如果有 role="user" 且有 parts，也认为是 Gemini 格式
    # 但需要进一步检查，避免误判
    if role == "user":
        # 检查是否有 Gemini 特有的字段（如 generationConfig, safetySettings）
        if "generationConfig" in body or "safetySettings" in body:
            return True
    
    return False


def from_gemini_chat(body: Dict[str, Any], path: str = "") -> InternalChatRequest:
    """
    Gemini Chat 格式 -> 内部格式
    
    Args:
        body: 请求体
        path: URL 路径（用于判断是否为流式请求）
    
    注意：
        Gemini API 通过不同的端点区分流式和非流式：
        - generateContent: 非流式
        - streamGenerateContent: 流式
        这是 Gemini 原生的设计，与 OpenAI/Claude 使用 stream 字段不同。
    
    Gemini 格式示例：
    {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "Hello"}
                ]
            },
            {
                "role": "model",
                "parts": [
                    {"text": "Hi!"}
                ]
            }
        ],
        "generationConfig": {...},
        "safetySettings": [...]
    }
    """
    messages = []
    
    for content in body.get("contents", []):
        role = content.get("role", "user")
        # 将 Gemini 的 "model" 角色转换为内部的 "assistant"
        if role == "model":
            role = "assistant"
        
        blocks = []
        
        for part in content.get("parts", []):
            # 处理文本内容
            if "text" in part:
                blocks.append(InternalContentBlock(
                    type="text",
                    text=part.get("text", "")
                ))
            
            # 处理函数调用（Gemini 的工具调用）
            elif "functionCall" in part:
                func_call = part["functionCall"]
                blocks.append(InternalContentBlock(
                    type="tool_call",
                    tool_call=InternalToolCall(
                        id=func_call.get("id", ""),
                        name=func_call.get("name", ""),
                        arguments=func_call.get("args", {})
                    )
                ))
            
            # 处理函数响应（Gemini 的工具结果）
            elif "functionResponse" in part:
                func_resp = part["functionResponse"]
                blocks.append(InternalContentBlock(
                    type="tool_result",
                    tool_result=InternalToolResult(
                        call_id=func_resp.get("id", ""),
                        name=func_resp.get("name"),
                        output=func_resp.get("response", {})
                    )
                ))
        
        if not blocks:
            blocks.append(InternalContentBlock(type="text", text=""))
        
        messages.append(InternalMessage(role=role, content=blocks))
    
    # 解析工具定义
    tools = []
    for tool_decl in body.get("tools", []):
        if "functionDeclarations" in tool_decl:
            for func_decl in tool_decl["functionDeclarations"]:
                tools.append(InternalTool(
                    name=func_decl.get("name", ""),
                    description=func_decl.get("description"),
                    input_schema=func_decl.get("parameters", {})
                ))
    
    # 提取模型和其他配置
    generation_config = body.get("generationConfig", {})
    
    # Gemini 通过端点判断流式
    # streamGenerateContent -> 流式
    # generateContent -> 非流式
    is_stream = "streamGenerateContent" in path
    
    return InternalChatRequest(
        messages=messages,
        model=body.get("model", "gemini-2.5-flash"),
        stream=is_stream,
        tools=tools,
        tool_choice=body.get("toolConfig"),
        extra={
            "generationConfig": generation_config,
            "safetySettings": body.get("safetySettings", []),
            **{k: v for k, v in body.items()
               if k not in ["contents", "model", "tools", "toolConfig", "generationConfig", "safetySettings"]}
        }
    )


def to_gemini_chat(req: InternalChatRequest) -> Dict[str, Any]:
    """
    内部格式 -> Gemini Chat 格式
    """
    # 转换消息
    contents = []
    for msg in req.messages:
        # 跳过 system 消息（Gemini 使用单独的 systemInstruction 字段）
        if msg.role == "system":
            continue
        
        # 将 "assistant" 角色转换为 Gemini 的 "model"
        role = "model" if msg.role == "assistant" else msg.role
        
        parts = []
        for block in msg.content:
            if block.type == "text" and block.text:
                parts.append({"text": block.text})
            
            elif block.type == "tool_call" and block.tool_call:
                parts.append({
                    "functionCall": {
                        "id": block.tool_call.id,
                        "name": block.tool_call.name,
                        "args": block.tool_call.arguments
                    }
                })
            
            elif block.type == "tool_result" and block.tool_result:
                parts.append({
                    "functionResponse": {
                        "id": block.tool_result.call_id,
                        "name": block.tool_result.name,
                        "response": block.tool_result.output
                    }
                })
        
        if parts:
            contents.append({
                "role": role,
                "parts": parts
            })
    
    # 转换工具定义
    tools = []
    if req.tools:
        function_declarations = []
        for tool in req.tools:
            function_declarations.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema
            })
        tools.append({"functionDeclarations": function_declarations})
    
    # 处理 system 消息
    system_messages = [m for m in req.messages if m.role == "system"]
    system_instruction = None
    if system_messages:
        system_texts = []
        for m in system_messages:
            texts = [b.text for b in m.content if b.type == "text" and b.text]
            system_texts.extend(texts)
        if system_texts:
            system_instruction = {
                "parts": [{"text": "\n".join(system_texts)}]
            }
    
    # 构建请求体
    body = {
        "contents": contents
    }
    
    if system_instruction:
        body["systemInstruction"] = system_instruction
    
    if tools:
        body["tools"] = tools
    
    if req.tool_choice is not None:
        body["toolConfig"] = req.tool_choice
    
    # 添加额外配置
    if "generationConfig" in req.extra:
        body["generationConfig"] = req.extra["generationConfig"]
    if "safetySettings" in req.extra:
        body["safetySettings"] = req.extra["safetySettings"]
    
    # 添加其他额外字段
    for k, v in req.extra.items():
        if k not in ["generationConfig", "safetySettings"]:
            body[k] = v
    
    return body


def gemini_resp_to_internal(resp: Dict[str, Any]) -> InternalChatResponse:
    """
    Gemini 响应 -> 内部格式
    
    Gemini 响应格式：
    {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "..."}
                    ],
                    "role": "model"
                },
                "finishReason": "STOP"
            }
        ],
        "usageMetadata": {...}
    }
    """
    candidates = resp.get("candidates", [])
    if not candidates:
        return InternalChatResponse(
            id=resp.get("id", ""),
            model=resp.get("modelVersion", ""),
            messages=[InternalMessage(
                role="assistant",
                content=[InternalContentBlock(type="text", text="")]
            )],
            finish_reason="error",
            usage=resp.get("usageMetadata"),
            extra={}
        )
    
    candidate = candidates[0]
    content = candidate.get("content") or {}
    
    blocks = []
    for part in content.get("parts", []):
        if "text" in part:
            blocks.append(InternalContentBlock(
                type="text",
                text=part.get("text", "")
            ))
        
        elif "functionCall" in part:
            func_call = part["functionCall"]
            blocks.append(InternalContentBlock(
                type="tool_call",
                tool_call=InternalToolCall(
                    id=func_call.get("id", ""),
                    name=func_call.get("name", ""),
                    arguments=func_call.get("args", {})
                )
            ))
    
    if not blocks:
        blocks.append(InternalContentBlock(type="text", text=""))
    
    # 转换 finishReason
    finish_reason = candidate.get("finishReason", "").lower()
    if finish_reason == "stop":
        finish_reason = "stop"
    elif finish_reason == "max_tokens":
        finish_reason = "length"
    
    return InternalChatResponse(
        id=resp.get("id", ""),
        model=resp.get("modelVersion", ""),
        messages=[InternalMessage(role="assistant", content=blocks)],
        finish_reason=finish_reason,
        usage=resp.get("usageMetadata"),
        extra={k: v for k, v in resp.items() 
               if k not in ["candidates", "modelVersion", "usageMetadata"]}
    )


def internal_to_gemini_resp(resp: InternalChatResponse) -> Dict[str, Any]:
    """
    内部格式 -> Gemini 响应
    """
    last_msg = resp.messages[-1] if resp.messages else InternalMessage(
        role="assistant",
        content=[InternalContentBlock(type="text", text="")]
    )
    
    parts = []
    for block in last_msg.content:
        if block.type == "text" and block.text:
            parts.append({"text": block.text})
        
        elif block.type == "tool_call" and block.tool_call:
            parts.append({
                "functionCall": {
                    "id": block.tool_call.id,
                    "name": block.tool_call.name,
                    "args": block.tool_call.arguments
                }
            })
    
    if not parts:
        parts = [{"text": ""}]
    
    # 转换 finish_reason
    finish_reason = resp.finish_reason or "STOP"
    if finish_reason == "stop":
        finish_reason = "STOP"
    elif finish_reason == "length":
        finish_reason = "MAX_TOKENS"
    else:
        finish_reason = finish_reason.upper()
    
    return {
        "candidates": [
            {
                "content": {
                    "parts": parts,
                    "role": "model"
                },
                "finishReason": finish_reason
            }
        ],
        "modelVersion": resp.model,
        "usageMetadata": resp.usage,
        **resp.extra
    }