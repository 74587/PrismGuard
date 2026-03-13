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


class TestResponsesSinkLifecycle(unittest.TestCase):
    def test_tool_only_does_not_inject_empty_message_item(self) -> None:
        tr = create_stream_transformer("openai_chat", "openai_responses")
        self.assertIsNotNone(tr)

        def sse_data(obj):
            return ("data: " + json.dumps(obj, separators=(",", ":")) + "\n\n").encode("utf-8")

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
                                        "function": {"name": "Bash", "arguments": "{\"cmd\":\"ls\"}"},
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

        # No injected empty message content part when the stream is tool-only.
        self.assertFalse(
            any(obj.get("type") == "response.content_part.added" for _, obj in out_frames),
            "tool-only streams must not inject an empty message content part",
        )

        # Must emit done events for the tool call for strict aggregators.
        self.assertTrue(any(obj.get("type") == "response.function_call_arguments.done" for _, obj in out_frames))
        self.assertTrue(any(obj.get("type") == "response.output_item.done" for _, obj in out_frames))

        completed = next((obj for _, obj in out_frames if obj.get("type") == "response.completed"), None)
        self.assertIsNotNone(completed)
        output = (completed.get("response") or {}).get("output") or []
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0].get("type"), "function_call")

    def test_completed_output_order_matches_output_index(self) -> None:
        tr = create_stream_transformer("openai_chat", "openai_responses")
        self.assertIsNotNone(tr)

        def sse_data(obj):
            return ("data: " + json.dumps(obj, separators=(",", ":")) + "\n\n").encode("utf-8")

        chunks = [
            # Text first => message should be output_index=0
            sse_data(
                {
                    "id": "chatcmpl_x",
                    "object": "chat.completion.chunk",
                    "created": 1710000000,
                    "model": "gpt-5.2",
                    "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
                }
            ),
            # Tool second => tool should be output_index=1
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
                                        "function": {"name": "Bash", "arguments": "{\"cmd\":\"ls\"}"},
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
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                }
            ),
            b"data: [DONE]\n\n",
        ]

        added = []
        completed = None
        for c in chunks:
            for out in tr.feed(c):
                for _, data in _iter_sse_frames(out):
                    if not data or data == "[DONE]":
                        continue
                    obj = json.loads(data)
                    if obj.get("type") == "response.output_item.added":
                        added.append((obj.get("output_index"), (obj.get("item") or {}).get("type")))
                    if obj.get("type") == "response.completed":
                        completed = obj
        for out in tr.flush():
            for _, data in _iter_sse_frames(out):
                if not data or data == "[DONE]":
                    continue
                obj = json.loads(data)
                if obj.get("type") == "response.output_item.added":
                    added.append((obj.get("output_index"), (obj.get("item") or {}).get("type")))
                if obj.get("type") == "response.completed":
                    completed = obj

        self.assertIsNotNone(completed)
        output = ((completed or {}).get("response") or {}).get("output") or []
        self.assertGreaterEqual(len(output), 2)

        # The first two added items must match output[0], output[1].
        added_sorted = [t for _, t in sorted(added, key=lambda x: int(x[0] or 0))]
        output_types = [it.get("type") for it in output[: len(added_sorted)]]
        self.assertEqual(output_types, added_sorted)

