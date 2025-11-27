"""
文本抽取模块 - 从不同格式的请求中抽取待审核文本
"""
from typing import Any, Dict
from ai_proxy.transform.formats.internal_models import InternalChatRequest


def extract_text_for_moderation(body: Dict[str, Any], request_format: str) -> str:
    """
    从请求体中抽取待审核文本（旧版，兼容性接口）
    
    Args:
        body: 请求体
        request_format: 请求格式 (openai_chat / claude_chat / openai_response)
        
    Returns:
        待审核的文本
    """
    if request_format == "openai_chat":
        return _extract_from_openai_chat(body)
    elif request_format == "claude_chat":
        return _extract_from_claude_chat(body)
    elif request_format == "openai_response":
        return _extract_from_openai_response(body)
    else:
        # 默认尝试从 messages 中提取
        return _extract_from_openai_chat(body)


def extract_text_from_internal(req: InternalChatRequest) -> str:
    """
    从内部格式抽取待审核文本（推荐使用）
    
    策略：
    - 提取所有 user 和 assistant 消息中的 text 类型内容块
    - 不提取 tool_call 的参数和 tool_result 的输出
    
    Args:
        req: 内部格式请求对象
        
    Returns:
        待审核的文本
    """
    pieces = []
    
    for m in req.messages:
        # 只审核 user 和 assistant 的内容
        if m.role not in ("user", "assistant"):
            continue
        
        for b in m.content:
            # 只审核文本内容
            if b.type == "text" and b.text:
                pieces.append(b.text)
    
    return "\n".join(pieces)


def _extract_from_openai_chat(body: Dict[str, Any]) -> str:
    """从 OpenAI Chat 格式提取文本"""
    messages = body.get("messages", [])
    texts = []
    
    for msg in messages:
        # 跳过 tool 角色的消息
        if msg.get("role") == "tool":
            continue
        
        content = msg.get("content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            # 处理多部分内容，只提取文本
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(part.get("text", ""))
    
    return "\n".join(texts)


def _extract_from_claude_chat(body: Dict[str, Any]) -> str:
    """从 Claude Chat 格式提取文本"""
    texts = []
    
    # 提取 system
    system = body.get("system", "")
    if system:
        texts.append(system)
    
    # 提取 messages
    messages = body.get("messages", [])
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    # 只提取 text 类型，跳过 tool_use 和 tool_result
                    if part.get("type") == "text":
                        texts.append(part.get("text", ""))
    
    return "\n".join(texts)


def _extract_from_openai_response(body: Dict[str, Any]) -> str:
    """从 OpenAI Response 格式提取文本"""
    # TODO: 根据实际 Response API 结构实现
    input_text = body.get("input", "")
    if isinstance(input_text, str):
        return input_text
    
    # 如果有 messages，按 chat 格式处理
    if "messages" in body:
        return _extract_from_openai_chat(body)
    
    return str(input_text)