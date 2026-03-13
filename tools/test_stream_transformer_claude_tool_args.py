import json
import unittest

from ai_proxy.proxy.stream_transformer import create_stream_transformer


def _iter_sse_frames(b: bytes):
    text = b.decode("utf-8", errors="ignore")
    for raw in text.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        event = None
        data_lines = []
        for line in raw.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        data = "\n".join(data_lines)
        yield event, data


class TestStreamTransformerClaudeToolArgs(unittest.TestCase):
    def test_openai_chat_tool_args_missing_id_name_chunks(self) -> None:
        tr = create_stream_transformer("openai_chat", "claude_chat")
        self.assertIsNotNone(tr)

        def sse_data(obj):
            return ("data: " + json.dumps(obj, separators=(",", ":")) + "\n\n").encode("utf-8")

        # Tool call where only the first chunk has id+name; later chunks omit both.
        chunks = [
            sse_data(
                {
                    "id": "chatcmpl_x",
                    "object": "chat.completion.chunk",
                    "created": 1710000000,
                    "model": "gpt-5.2",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "Bash", "arguments": ""},
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
                    "id": "chatcmpl_x",
                    "object": "chat.completion.chunk",
                    "created": 1710000000,
                    "model": "gpt-5.2",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": [{"index": 0, "type": "function", "function": {"arguments": "{\"cmd\":\"ls\""}}]},
                            "finish_reason": None,
                        }
                    ],
                }
            ),
            sse_data(
                {
                    "id": "chatcmpl_x",
                    "object": "chat.completion.chunk",
                    "created": 1710000000,
                    "model": "gpt-5.2",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": [{"index": 0, "type": "function", "function": {"arguments": "}"}}]},
                            "finish_reason": None,
                        }
                    ],
                }
            ),
            sse_data(
                {
                    "id": "chatcmpl_x",
                    "object": "chat.completion.chunk",
                    "created": 1710000000,
                    "model": "gpt-5.2",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                }
            ),
            b"data: [DONE]\n\n",
        ]

        out_frames = []
        for c in chunks:
            for out in tr.feed(c):
                for ev, data in _iter_sse_frames(out):
                    if not data or data == "[DONE]":
                        continue
                    out_frames.append((ev, json.loads(data)))
        for out in tr.flush():
            for ev, data in _iter_sse_frames(out):
                if not data or data == "[DONE]":
                    continue
                out_frames.append((ev, json.loads(data)))

        tool_use_start = next(
            (obj for ev, obj in out_frames if (obj.get("type") == "content_block_start" and obj.get("content_block", {}).get("type") == "tool_use")),
            None,
        )
        self.assertIsNotNone(tool_use_start)
        self.assertEqual(tool_use_start["content_block"]["id"], "call_1")
        self.assertEqual(tool_use_start["content_block"]["name"], "Bash")

        deltas = [
            obj.get("delta", {}).get("partial_json", "")
            for ev, obj in out_frames
            if obj.get("type") == "content_block_delta" and obj.get("delta", {}).get("type") == "input_json_delta"
        ]
        self.assertTrue(deltas, "expected at least one input_json_delta for tool args")
        self.assertEqual("".join(deltas), "{\"cmd\":\"ls\"}")
