"""
格式解析器 - 支持多来源自动检测
"""
from typing import Dict, Any, Optional, Tuple, List, Protocol
from ai_proxy.transform.formats.internal_models import InternalChatRequest, InternalChatResponse
from ai_proxy.transform.formats import openai_chat, claude_chat


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


# 注册所有解析器
PARSERS: Dict[str, FormatParser] = {
    "openai_chat": OpenAIChatParser(),
    "claude_chat": ClaudeChatParser(),
}


def detect_and_parse(
    config_from: Any,
    path: str,
    headers: Dict[str, str],
    body: Dict[str, Any]
) -> Tuple[Optional[str], Optional[InternalChatRequest]]:
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
    
    Returns:
        (格式名称, 内部请求对象) 或 (None, None) 表示无法识别
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
                return name, internal
        except Exception as e:
            # 解析失败，继续尝试下一个
            print(f"[WARN] Failed to parse as {name}: {e}")
            continue
    
    # 3. 都不识别，返回 None
    return None, None


def get_parser(format_name: str) -> Optional[FormatParser]:
    """获取指定格式的解析器"""
    return PARSERS.get(format_name)