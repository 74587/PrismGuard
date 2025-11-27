"""
OpenAI Chat 格式转换 - 支持工具调用
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


def can_parse_openai_chat(path: str, headers: Dict[str, str], body: Dict[str, Any]) -> bool:
    """判断是否为 OpenAI Chat 格式"""
    # 排斥 OpenAI Codex/Completions 格式：如果有 prompt 字段且路径不含 /chat，则不是 Chat
    if "prompt" in body and "messages" not in body:
        return False
    
    # 检查路径
    if "/chat/completions" in path:
        return True
    # 检查 body 结构
    if "messages" in body:
        messages = body.get("messages", [])
        if messages and isinstance(messages, list):
            first_msg = messages[0]
            if isinstance(first_msg, dict) and "role" in first_msg:
                return True
    return False


def from_openai_chat(body: Dict[str, Any]) -> InternalChatRequest:
    """
    OpenAI Chat 格式 -> 内部格式（支持工具调用）
    """
    # 解析工具定义
    tools = []
    for t in body.get("tools", []):
        if t.get("type") == "function":
            func = t["function"]
            tools.append(InternalTool(
                name=func["name"],
                description=func.get("description"),
                input_schema=func.get("parameters", {})
            ))
    
    # 解析消息
    messages = []
    for msg in body.get("messages", []):
        blocks = []
        
        # 1. 处理文本内容
        content = msg.get("content")
        if isinstance(content, str) and content:
            blocks.append(InternalContentBlock(type="text", text=content))
        elif isinstance(content, list):
            # 多部分内容
            texts = [p.get("text", "") for p in content if p.get("type") == "text"]
            if texts:
                blocks.append(InternalContentBlock(type="text", text="\n".join(texts)))
        
        # 2. 处理 tool role 的消息（工具结果）
        if msg.get("role") == "tool":
            blocks.append(InternalContentBlock(
                type="tool_result",
                tool_result=InternalToolResult(
                    call_id=msg.get("tool_call_id", ""),
                    name=msg.get("name"),
                    output=msg.get("content", "")
                )
            ))
        
        # 3. 处理 assistant 的工具调用
        for tc in msg.get("tool_calls", []):
            args_str = tc.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except:
                args = {}
            
            blocks.append(InternalContentBlock(
                type="tool_call",
                tool_call=InternalToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("function", {}).get("name", ""),
                    arguments=args
                )
            ))
        
        # 如果没有任何块，添加空文本块
        if not blocks:
            blocks.append(InternalContentBlock(type="text", text=""))
        
        messages.append(InternalMessage(
            role=msg.get("role", "user"),
            content=blocks
        ))
    
    return InternalChatRequest(
        messages=messages,
        model=body.get("model", ""),
        stream=body.get("stream", False),
        tools=tools,
        tool_choice=body.get("tool_choice"),
        extra={k: v for k, v in body.items() 
               if k not in ["messages", "model", "stream", "tools", "tool_choice"]}
    )


def to_openai_chat(req: InternalChatRequest) -> Dict[str, Any]:
    """
    内部格式 -> OpenAI Chat 格式（支持工具调用）
    """
    # 转换工具定义
    tools = []
    for t in req.tools:
        tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema
            }
        })
    
    # 转换消息
    messages = []
    for m in req.messages:
        # 收集不同类型的内容块
        text_blocks = [b.text for b in m.content if b.type == "text" and b.text]
        tool_call_blocks = [b.tool_call for b in m.content if b.type == "tool_call"]
        tool_result_blocks = [b.tool_result for b in m.content if b.type == "tool_result"]
        
        # 非 tool role 的消息
        if m.role != "tool":
            msg = {"role": m.role}
            
            # 添加文本内容
            if text_blocks:
                msg["content"] = "\n".join(text_blocks)
            elif not tool_call_blocks:
                msg["content"] = ""
            
            # 添加工具调用
            if tool_call_blocks:
                msg["tool_calls"] = [{
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                    }
                } for tc in tool_call_blocks]
            
            messages.append(msg)
        
        # 工具结果转为独立的 tool 消息
        for tr in tool_result_blocks:
            messages.append({
                "role": "tool",
                "tool_call_id": tr.call_id,
                "name": tr.name,
                "content": json.dumps(tr.output, ensure_ascii=False) if isinstance(tr.output, dict) else str(tr.output)
            })
    
    # 构建请求体
    body = {
        "model": req.model,
        "messages": messages,
        "stream": req.stream
    }
    
    if tools:
        body["tools"] = tools
    if req.tool_choice is not None:
        body["tool_choice"] = req.tool_choice
    
    body.update(req.extra)
    
    return body


def openai_chat_resp_to_internal(resp: Dict[str, Any]) -> InternalChatResponse:
    """
    OpenAI Chat 响应 -> 内部格式
    """
    choice = resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    
    # 解析消息内容
    blocks = []
    
    # 文本内容
    content = message.get("content")
    if content:
        blocks.append(InternalContentBlock(type="text", text=content))
    
    # 工具调用
    for tc in message.get("tool_calls", []):
        args_str = tc.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except:
            args = {}
        
        blocks.append(InternalContentBlock(
            type="tool_call",
            tool_call=InternalToolCall(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                arguments=args
            )
        ))
    
    if not blocks:
        blocks.append(InternalContentBlock(type="text", text=""))
    
    return InternalChatResponse(
        id=resp.get("id", ""),
        model=resp.get("model", ""),
        messages=[InternalMessage(role="assistant", content=blocks)],
        finish_reason=choice.get("finish_reason"),
        usage=resp.get("usage"),
        extra={k: v for k, v in resp.items() 
               if k not in ["id", "model", "choices", "usage"]}
    )


def internal_to_openai_resp(resp: InternalChatResponse) -> Dict[str, Any]:
    """
    内部格式 -> OpenAI Chat 响应
    """
    # 取最后一条 assistant 消息
    last_msg = resp.messages[-1] if resp.messages else InternalMessage(
        role="assistant",
        content=[InternalContentBlock(type="text", text="")]
    )
    
    # 构建消息
    message = {"role": "assistant"}
    
    text_blocks = [b.text for b in last_msg.content if b.type == "text" and b.text]
    if text_blocks:
        message["content"] = "\n".join(text_blocks)
    
    tool_calls = []
    for b in last_msg.content:
        if b.type == "tool_call" and b.tool_call:
            tool_calls.append({
                "id": b.tool_call.id,
                "type": "function",
                "function": {
                    "name": b.tool_call.name,
                    "arguments": json.dumps(b.tool_call.arguments, ensure_ascii=False)
                }
            })
    
    if tool_calls:
        message["tool_calls"] = tool_calls
    
    return {
        "id": resp.id,
        "model": resp.model,
        "object": "chat.completion",
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": resp.finish_reason
        }],
        "usage": resp.usage,
        **resp.extra
    }