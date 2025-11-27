"""
格式解析器 - 支持多来源自动检测
"""
from typing import Dict, Any, Optional, Tuple, List, Protocol
from ai_proxy.transform.formats.internal_models import InternalChatRequest, InternalChatResponse
from ai_proxy.transform.formats import openai_chat, claude_chat, claude_code, openai_codex


class FormatParser(Protocol):
    """格式解析器接口"""
    name: str
    
    def can_parse(self, path: str, headers: Dict[str, str], body: Dict[str, Any]) -> bool:
        """判断是否能解析该格式"""
        ...
    
    def from_format(self, body: Dict[str, Any]) -> InternalChatRequest:
        """从特定格式转为内部格式"""
        ...
    
    def to_format(self, req: InternalChatRequest) -> Dict[str, Any]:
        """从内部格式转为特定格式"""
        ...
    
    def resp_to_internal(self, resp: Dict[str, Any]) -> InternalChatResponse:
        """响应转为内部格式"""
        ...
    
    def internal_to_resp(self, resp: InternalChatResponse) -> Dict[str, Any]:
        """内部格式转为响应"""
        ...


class OpenAIChatParser:
    """OpenAI Chat 解析器"""
    name = "openai_chat"
    
    def can_parse(self, path: str, headers: Dict[str, str], body: Dict[str, Any]) -> bool:
        return openai_chat.can_parse_openai_chat(path, headers, body)
    
    def from_format(self, body: Dict[str, Any]) -> InternalChatRequest:
        return openai_chat.from_openai_chat(body)
    
    def to_format(self, req: InternalChatRequest) -> Dict[str, Any]:
        return openai_chat.to_openai_chat(req)
    
    def resp_to_internal(self, resp: Dict[str, Any]) -> InternalChatResponse:
        return openai_chat.openai_chat_resp_to_internal(resp)
    
    def internal_to_resp(self, resp: InternalChatResponse) -> Dict[str, Any]:
        return openai_chat.internal_to_openai_resp(resp)


class ClaudeChatParser:
    """Claude Chat 解析器"""
    name = "claude_chat"
    
    def can_parse(self, path: str, headers: Dict[str, str], body: Dict[str, Any]) -> bool:
        return claude_chat.can_parse_claude_chat(path, headers, body)
    
    def from_format(self, body: Dict[str, Any]) -> InternalChatRequest:
        return claude_chat.from_claude_chat(body)
    
    def to_format(self, req: InternalChatRequest) -> Dict[str, Any]:
        return claude_chat.to_claude_chat(req)
    
    def resp_to_internal(self, resp: Dict[str, Any]) -> InternalChatResponse:
        return claude_chat.claude_resp_to_internal(resp)
    
    def internal_to_resp(self, resp: InternalChatResponse) -> Dict[str, Any]:
        return claude_chat.internal_to_claude_resp(resp)


class ClaudeCodeParser:
    """Claude Code (Agent SDK) 解析器"""
    name = "claude_code"
    
    def can_parse(self, path: str, headers: Dict[str, str], body: Dict[str, Any]) -> bool:
        return claude_code.can_parse_claude_code(path, headers, body)
    
    def from_format(self, body: Dict[str, Any]) -> InternalChatRequest:
        return claude_code.from_claude_code(body)
    
    def to_format(self, req: InternalChatRequest) -> Dict[str, Any]:
        return claude_code.to_claude_code(req)
    
    def resp_to_internal(self, resp: Dict[str, Any]) -> InternalChatResponse:
        return claude_code.claude_code_resp_to_internal(resp)
    
    def internal_to_resp(self, resp: InternalChatResponse) -> Dict[str, Any]:
        return claude_code.internal_to_claude_code_resp(resp)


class OpenAICodexParser:
    """OpenAI Codex/Completions 解析器"""
    name = "openai_codex"
    
    def can_parse(self, path: str, headers: Dict[str, str], body: Dict[str, Any]) -> bool:
        return openai_codex.can_parse_openai_codex(path, headers, body)
    
    def from_format(self, body: Dict[str, Any]) -> InternalChatRequest:
        return openai_codex.from_openai_codex(body)
    
    def to_format(self, req: InternalChatRequest) -> Dict[str, Any]:
        return openai_codex.to_openai_codex(req)
    
    def resp_to_internal(self, resp: Dict[str, Any]) -> InternalChatResponse:
        return openai_codex.openai_codex_resp_to_internal(resp)
    
    def internal_to_resp(self, resp: InternalChatResponse) -> Dict[str, Any]:
        return openai_codex.internal_to_openai_codex_resp(resp)


# 注册所有解析器
PARSERS: Dict[str, FormatParser] = {
    "openai_chat": OpenAIChatParser(),
    "claude_chat": ClaudeChatParser(),
    "claude_code": ClaudeCodeParser(),
    "openai_codex": OpenAICodexParser(),
}


def detect_and_parse(
    config_from: Any,
    path: str,
    headers: Dict[str, str],
    body: Dict[str, Any],
    strict_parse: bool = False
) -> Tuple[Optional[str], Optional[InternalChatRequest], Optional[str]]:
    """
    检测并解析请求格式
    
    Args:
        config_from: 配置的来源格式，可以是：
            - str: 单一格式名称
            - list: 格式名称列表
            - "auto": 自动检测所有支持的格式
        path: 请求路径
        headers: 请求头
        body: 请求体
        strict_parse: 是否启用严格解析模式
    
    Returns:
        (格式名称, 内部请求对象, 错误消息) 或 (None, None, 错误消息) 表示无法识别
    """
    # 1. 确定候选格式列表
    if config_from == "auto":
        candidates = list(PARSERS.keys())
    elif isinstance(config_from, str):
        candidates = [config_from]
    elif isinstance(config_from, list):
        candidates = config_from
    else:
        candidates = list(PARSERS.keys())
    
    # 2. 按顺序尝试解析
    for name in candidates:
        parser = PARSERS.get(name)
        if parser is None:
            continue
        
        try:
            if parser.can_parse(path, headers, body):
                internal = parser.from_format(body)
                return name, internal, None
        except Exception as e:
            # 解析失败，继续尝试下一个
            print(f"[WARN] Failed to parse as {name}: {e}")
            continue
    
    # 3. 都不识别
    if strict_parse:
        # 严格模式：检查是否有其他格式可以解析
        all_formats = list(PARSERS.keys())
        excluded_formats = [f for f in all_formats if f not in candidates]
        
        # 遍历被排除的格式，看是否有可以解析的
        detectable_formats = []
        for name in excluded_formats:
            parser = PARSERS.get(name)
            if parser is None:
                continue
            
            try:
                if parser.can_parse(path, headers, body):
                    detectable_formats.append(name)
            except Exception:
                continue
        
        if detectable_formats:
            # 发现有可以解析但被排除的格式
            expected_str = f"'{config_from}'" if isinstance(config_from, str) else str(candidates)
            detected_str = ", ".join(f"'{f}'" for f in detectable_formats)
            error_msg = (
                f"Format mismatch: Request appears to be in format [{detected_str}], "
                f"but only [{expected_str}] is allowed. "
                f"Please check your 'from' configuration or update it to include the detected format."
            )
            return None, None, error_msg
        else:
            # 没有任何格式可以解析
            expected_str = f"'{config_from}'" if isinstance(config_from, str) else str(candidates)
            error_msg = (
                f"Unable to parse request format. Expected format: {expected_str}. "
                f"Please verify your request body structure matches the expected format."
            )
            return None, None, error_msg
    
    # 非严格模式：返回 None 表示无法识别（将透传）
    return None, None, None


def get_parser(format_name: str) -> Optional[FormatParser]:
    """获取指定格式的解析器"""
    return PARSERS.get(format_name)