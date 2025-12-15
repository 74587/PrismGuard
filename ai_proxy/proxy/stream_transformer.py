"""
流式响应转换器 - 在不同协议的 SSE 之间互转
"""
import json
import time
from typing import List, Optional


def _encode_sse(data: str) -> bytes:
    return f"data: {data}\n\n".encode("utf-8")


def _encode_json(payload: dict) -> bytes:
    return _encode_sse(json.dumps(payload, ensure_ascii=False))


class BaseSSEAdapter:
    """基础 SSE 适配器"""

    def handle_payload(self, payload: str) -> List[bytes]:
        payload = payload.strip()
        if not payload:
            return []
        if payload == "[DONE]":
            return [_encode_sse("[DONE]")]
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return []
        return self.handle_event(event)

    def handle_event(self, event: dict) -> List[bytes]:
        raise NotImplementedError

    def flush(self) -> List[bytes]:
        return []


class SSEBufferTransformer:
    """SSE 分块解析器，将数据映射到指定适配器"""

    def __init__(self, adapter: BaseSSEAdapter):
        self.adapter = adapter
        self.buffer = ""

    def feed(self, chunk: bytes) -> List[bytes]:
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            text = chunk.decode("utf-8", errors="ignore")

        self.buffer += text
        outputs: List[bytes] = []

        while "\n\n" in self.buffer:
            raw_event, self.buffer = self.buffer.split("\n\n", 1)
            data_lines = [line[5:].lstrip() for line in raw_event.splitlines() if line.startswith("data:")]
            if not data_lines:
                continue
            payload = "\n".join(data_lines)
            outputs.extend(self.adapter.handle_payload(payload))

        return outputs

    def flush(self) -> List[bytes]:
        remaining = self.buffer.strip()
        self.buffer = ""
        outputs = []
        if remaining:
            outputs.extend(self.adapter.handle_payload(remaining))
        outputs.extend(self.adapter.flush())
        return outputs


class ResponsesToChatAdapter(BaseSSEAdapter):
    """OpenAI Responses -> OpenAI Chat SSE"""

    def __init__(self) -> None:
        self.response_id: Optional[str] = None
        self.model: Optional[str] = None
        self.created_at: Optional[int] = None

    def handle_event(self, event: dict) -> List[bytes]:
        etype = event.get("type")
        if not etype:
            return []

        if etype in {"response.created", "response.in_progress"}:
            resp = event.get("response") or {}
            self.response_id = resp.get("id", self.response_id)
            self.model = resp.get("model", self.model)
            self.created_at = resp.get("created_at", self.created_at) or int(time.time())
            delta = {"role": "assistant"}
            return [self._build_chunk(delta)]

        if etype == "response.output_text.delta":
            delta_text = event.get("delta") or event.get("text") or ""
            if not delta_text:
                return []
            delta = {"content": delta_text}
            return [self._build_chunk(delta)]

        if etype in {"response.function_call_arguments.delta", "response.function_call.delta"}:
            arguments = event.get("delta") or event.get("arguments") or ""
            call_id = event.get("call_id") or ""
            name = event.get("name") or ""
            tool_calls = [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": arguments,
                    },
                }
            ]
            delta = {"tool_calls": tool_calls}
            return [self._build_chunk(delta)]

        if etype == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                tool_calls = [
                    {
                        "id": item.get("call_id"),
                        "type": "function",
                        "function": {
                            "name": item.get("name"),
                            "arguments": "",
                        },
                    }
                ]
                delta = {"tool_calls": tool_calls}
                return [self._build_chunk(delta)]
            return []

        if etype == "response.reasoning_summary_text.delta":
            text = event.get("delta")
            if not text:
                return []
            delta = {"content": text}
            return [self._build_chunk(delta)]

        if etype in {"response.completed", "response.failed", "response.incomplete", "error"}:
            finish_reason = None
            usage = None
            resp = event.get("response") or {}
            status = (resp.get("status") or "").lower()
            if etype == "error":
                finish_reason = "error"
            elif status == "completed":
                finish_reason = "stop"
            elif status == "incomplete":
                finish_reason = "length"
            elif status == "failed":
                finish_reason = "error"

            usage = self._convert_usage(resp.get("usage"))
            return [self._build_chunk({}, finish_reason=finish_reason, usage=usage)]

        return []

    def _build_chunk(self, delta: dict, finish_reason: Optional[str] = None, usage: Optional[dict] = None) -> bytes:
        chunk = {
            "id": self.response_id or "",
            "object": "chat.completion.chunk",
            "created": self.created_at or int(time.time()),
            "model": self.model or "",
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        if usage:
            chunk["usage"] = usage
        return _encode_json(chunk)

    @staticmethod
    def _convert_usage(usage: Optional[dict]) -> Optional[dict]:
        if not isinstance(usage, dict):
            return None
        return {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }


class ChatToResponsesAdapter(BaseSSEAdapter):
    """OpenAI Chat -> OpenAI Responses SSE"""

    def __init__(self) -> None:
        self.response_id: Optional[str] = None
        self.model: Optional[str] = None
        self.created_at: Optional[int] = None
        self.started = False

    def handle_event(self, event: dict) -> List[bytes]:
        if event.get("choices") is None:
            return []

        self.response_id = event.get("id", self.response_id)
        self.model = event.get("model", self.model)
        self.created_at = event.get("created", self.created_at) or int(time.time())

        outputs: List[bytes] = []
        if not self.started:
            self.started = True
            resp = self._response_stub(status="in_progress")
            outputs.append(_encode_json({"type": "response.created", "response": resp}))
            outputs.append(_encode_json({"type": "response.in_progress", "response": resp}))

        choice = event["choices"][0]
        delta = choice.get("delta") or {}

        if delta.get("content"):
            outputs.append(
                _encode_json(
                    {
                        "type": "response.output_text.delta",
                        "delta": delta["content"],
                        "output_index": 0,
                    }
                )
            )

        if delta.get("tool_calls"):
            for tool_call in delta["tool_calls"]:
                outputs.extend(self._handle_tool_call(tool_call))

        finish_reason = choice.get("finish_reason")
        if finish_reason:
            status = {
                "stop": "completed",
                "length": "incomplete",
                "content_filter": "failed",
                "function_call": "completed",
                "tool_calls": "completed",
                "error": "failed",
            }.get(finish_reason, "completed")
            resp = self._response_stub(status=status)
            outputs.append(_encode_json({"type": "response.completed", "response": resp}))

        return outputs

    def _response_stub(self, status: str) -> dict:
        return {
            "object": "response",
            "id": self.response_id or "",
            "model": self.model or "",
            "created_at": self.created_at or int(time.time()),
            "status": status,
            "output": [],
        }

    def _handle_tool_call(self, tool_call: dict) -> List[bytes]:
        call_id = tool_call.get("id")
        function = tool_call.get("function") or {}
        name = function.get("name")
        arguments = function.get("arguments", "")

        outputs = []
        outputs.append(
            _encode_json(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                    },
                }
            )
        )
        if arguments:
            outputs.append(
                _encode_json(
                    {
                        "type": "response.function_call_arguments.delta",
                        "call_id": call_id,
                        "name": name,
                        "delta": arguments,
                    }
                )
            )
        return outputs


def create_stream_transformer(from_format: str, to_format: str) -> Optional[SSEBufferTransformer]:
    """创建指定格式之间的流式转换器"""
    adapter: Optional[BaseSSEAdapter] = None
    if from_format == "openai_responses" and to_format == "openai_chat":
        adapter = ResponsesToChatAdapter()
    elif from_format == "openai_chat" and to_format == "openai_responses":
        adapter = ChatToResponsesAdapter()

    if adapter is None:
        return None
    return SSEBufferTransformer(adapter)
