"""
OpenAI Codex / Completions 格式转换
这是 OpenAI 的旧式 Completions API，与 Chat Completions 不同
"""
from typing import Dict, Any
from ai_proxy.transform.formats.internal_models import (
    InternalChatRequest,
    InternalChatResponse,
    InternalMessage,
    InternalContentBlock
)


def can_parse_openai_codex(path: str, headers: Dict[str, str], body: Dict[str, Any]) -> bool:
    """判断是否为 OpenAI Codex/Completions 格式"""
    # 排斥 OpenAI Chat 格式：如果路径是 /chat/completions，则不是 Codex
    if "/chat/completions" in path:
        return False
    
    # OpenAI Completions API 特征：
    # 1. 路径包含 /completions 或 /complete
    # 2. 包含 prompt 字段（而不是 messages）
    # 3. 包含 max_tokens 或 max_tokens_to_sample
    
    # 检查路径
    if "/completions" in path or "/complete" in path:
        # 确保不是 chat completions
        if "/chat" not in path:
            return True
    
    # 检查 body 结构
    if "prompt" in body and "messages" not in body:
        # 进一步检查是否有 Completions API 特有的参数
        if "max_tokens" in body or "max_tokens_to_sample" in body:
            return True
        # 或者检查是否有典型的 Completions API 参数
        if any(k in body for k in ["temperature", "top_p", "stop", "model"]):
            return True
    
    return False


def from_openai_codex(body: Dict[str, Any]) -> InternalChatRequest:
    """
    OpenAI Codex/Completions 格式 -> 内部格式
    
    Completions API 格式示例:
    {
        "model": "text-davinci-003",
        "prompt": "Write a function to",
        "max_tokens": 100,
        "temperature": 0.7,
        "stop": ["\n"]
    }
    """
    prompt = body.get("prompt", "")
    
    # 将 prompt 转换为 user 消息
    messages = []
    
    # prompt 可能是字符串、字符串列表或包含多个 prompt 的结构
    if isinstance(prompt, str):
        messages.append(InternalMessage(
            role="user",
            content=[InternalContentBlock(type="text", text=prompt)]
        ))
    elif isinstance(prompt, list):
        # 多个 prompts，合并或只取第一个
        prompt_text = "\n".join(str(p) for p in prompt)
        messages.append(InternalMessage(
            role="user",
            content=[InternalContentBlock(type="text", text=prompt_text)]
        ))
    else:
        messages.append(InternalMessage(
            role="user",
            content=[InternalContentBlock(type="text", text=str(prompt))]
        ))
    
    # Completions API 不支持工具调用
    return InternalChatRequest(
        messages=messages,
        model=body.get("model", ""),
        stream=body.get("stream", False),
        tools=[],
        tool_choice=None,
        extra={
            "max_tokens": body.get("max_tokens", body.get("max_tokens_to_sample")),
            "temperature": body.get("temperature"),
            "top_p": body.get("top_p"),
            "top_k": body.get("top_k"),
            "stop": body.get("stop", body.get("stop_sequences")),
            "frequency_penalty": body.get("frequency_penalty"),
            "presence_penalty": body.get("presence_penalty"),
            "logprobs": body.get("logprobs"),
            "echo": body.get("echo"),
            "suffix": body.get("suffix"),
            **{k: v for k, v in body.items() 
               if k not in ["prompt", "model", "stream", "max_tokens", "max_tokens_to_sample",
                           "temperature", "top_p", "top_k", "stop", "stop_sequences",
                           "frequency_penalty", "presence_penalty", "logprobs", "echo", "suffix"]}
        }
    )


def to_openai_codex(req: InternalChatRequest) -> Dict[str, Any]:
    """
    内部格式 -> OpenAI Codex/Completions 格式
    """
    # 提取所有 user 消息的文本
    user_msgs = [m for m in req.messages if m.role == "user"]
    
    # 合并所有文本
    prompt_texts = []
    for m in user_msgs:
        texts = [b.text for b in m.content if b.type == "text" and b.text]
        prompt_texts.extend(texts)
    
    prompt = "\n".join(prompt_texts) if prompt_texts else ""
    
    # 构建请求体
    body = {
        "model": req.model,
        "prompt": prompt,
        "stream": req.stream
    }
    
    # 添加额外参数
    if req.extra:
        body.update(req.extra)
    
    return body


def openai_codex_resp_to_internal(resp: Dict[str, Any]) -> InternalChatResponse:
    """
    OpenAI Codex/Completions 响应 -> 内部格式
    
    Completions API 响应格式:
    {
        "id": "cmpl-xxx",
        "object": "text_completion",
        "model": "text-davinci-003",
        "choices": [{
            "text": "generated text",
            "index": 0,
            "finish_reason": "stop"
        }]
    }
    """
    choice = resp.get("choices", [{}])[0]
    
    # 提取生成的文本
    text = choice.get("text", "")
    
    blocks = [InternalContentBlock(type="text", text=text)]
    
    return InternalChatResponse(
        id=resp.get("id", ""),
        model=resp.get("model", ""),
        messages=[InternalMessage(role="assistant", content=blocks)],
        finish_reason=choice.get("finish_reason"),
        usage=resp.get("usage"),
        extra={k: v for k, v in resp.items() 
               if k not in ["id", "model", "choices", "usage"]}
    )


def internal_to_openai_codex_resp(resp: InternalChatResponse) -> Dict[str, Any]:
    """
    内部格式 -> OpenAI Codex/Completions 响应格式
    """
    # 取最后一条 assistant 消息
    last_msg = resp.messages[-1] if resp.messages else InternalMessage(
        role="assistant",
        content=[InternalContentBlock(type="text", text="")]
    )
    
    # 合并所有文本块
    text_blocks = [b.text for b in last_msg.content if b.type == "text" and b.text]
    text = "\n".join(text_blocks) if text_blocks else ""
    
    return {
        "id": resp.id,
        "object": "text_completion",
        "model": resp.model,
        "choices": [{
            "text": text,
            "index": 0,
            "finish_reason": resp.finish_reason,
            "logprobs": None
        }],
        "usage": resp.usage,
        **resp.extra
    }