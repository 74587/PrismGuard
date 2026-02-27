"""
Smoke test for stream transformers (no network).

Runs a small set of synthetic SSE streams through all format pairs and checks
that the transformer produces some output without raising exceptions.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ai_proxy.proxy.stream_transformer import create_stream_transformer


def sse_data(obj) -> bytes:
    if isinstance(obj, str):
        payload = obj
    else:
        payload = json.dumps(obj, ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")


def sse_event(event: str, obj) -> bytes:
    payload = json.dumps(obj, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def sample_openai_chat() -> List[bytes]:
    return [
        sse_data(
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "gpt-test",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
        ),
        sse_data(
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "gpt-test",
                "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
            }
        ),
        sse_data(
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": '{"city":"BJ"}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            }
        ),
        sse_data(
            {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "created": 123,
                "model": "gpt-test",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }
        ),
        sse_data("[DONE]"),
    ]


def sample_openai_responses() -> List[bytes]:
    return [
        sse_data({"type": "response.created", "response": {"id": "resp_test", "model": "gpt-test", "created_at": 123, "status": "in_progress", "output": []}}),
        sse_data({"type": "response.in_progress", "response": {"id": "resp_test", "model": "gpt-test", "created_at": 123, "status": "in_progress", "output": []}}),
        sse_data({"type": "response.output_text.delta", "delta": "Hello", "output_index": 0}),
        sse_data({"type": "response.output_item.added", "item": {"type": "function_call", "call_id": "call_1", "name": "get_weather"}}),
        sse_data({"type": "response.function_call_arguments.delta", "call_id": "call_1", "name": "get_weather", "delta": '{"city":"BJ"}'}),
        sse_data({"type": "response.completed", "response": {"id": "resp_test", "model": "gpt-test", "created_at": 123, "status": "completed", "output": [], "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}}),
    ]


def sample_gemini() -> List[bytes]:
    return [
        sse_data(
            {
                "candidates": [{"content": {"parts": [{"text": "Hello"}], "role": "model"}, "index": 0}],
                "responseId": "gemini_resp",
                "modelVersion": "gemini-test",
            }
        ),
        sse_data(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"functionCall": {"id": "call_1", "name": "get_weather", "args": {"city": "BJ"}}}],
                            "role": "model",
                        },
                        "index": 0,
                    }
                ],
                "responseId": "gemini_resp",
                "modelVersion": "gemini-test",
            }
        ),
    ]


def sample_claude() -> List[bytes]:
    return [
        sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
        ),
        sse_event("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        sse_event("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}),
        sse_event("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {}}}),
        sse_event("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"city\":\"BJ\"}"}}),
        sse_event("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}}),
        sse_event("message_stop", {"type": "message_stop"}),
    ]


SAMPLES: Dict[str, List[bytes]] = {
    "openai_chat": sample_openai_chat(),
    "openai_responses": sample_openai_responses(),
    "gemini_chat": sample_gemini(),
    "claude_chat": sample_claude(),
}


def run_pair(src: str, dst: str) -> Tuple[int, int]:
    t = create_stream_transformer(src, dst)
    if t is None:
        raise RuntimeError(f"no transformer for {src} -> {dst}")

    out_chunks: List[bytes] = []
    for chunk in SAMPLES[src]:
        out_chunks.extend(t.feed(chunk))
    out_chunks.extend(t.flush())
    return len(SAMPLES[src]), len(out_chunks)


def main() -> int:
    formats = list(SAMPLES.keys())
    failures = 0

    for src in formats:
        for dst in formats:
            if src == dst:
                continue
            try:
                n_in, n_out = run_pair(src, dst)
                if n_out <= 0:
                    print(f"[FAIL] {src} -> {dst}: out=0 (in={n_in})")
                    failures += 1
                else:
                    print(f"[OK]   {src} -> {dst}: in={n_in} out={n_out}")
            except Exception as e:
                print(f"[ERR]  {src} -> {dst}: {type(e).__name__}: {e}")
                failures += 1

    if failures:
        print(f"\nFAILED: {failures}")
        return 2

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
