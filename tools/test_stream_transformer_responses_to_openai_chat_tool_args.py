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


class TestStreamTransformerResponsesToOpenAIChatToolArgs(unittest.TestCase):
    def test_args_delta_before_item_added_is_buffered_until_name_known(self) -> None:
        tr = create_stream_transformer("openai_responses", "openai_chat")
        self.assertIsNotNone(tr)

        def sse_data(obj):
            return ("data: " + json.dumps(obj, separators=(",", ":")) + "\n\n").encode("utf-8")

        resp_stub = {"id": "resp_1", "model": "gpt-5.2", "created_at": 1710000000, "status": "in_progress"}
        chunks = [
            sse_data({"type": "response.created", "response": resp_stub}),
            # Arguments arrive before output_item.added and without call_id, only item_id.
            sse_data({"type": "response.function_call_arguments.delta", "item_id": "itm_1", "delta": "{\"cmd\":\"ls\""}),
            sse_data({"type": "response.function_call_arguments.delta", "item_id": "itm_1", "delta": "}"}),
            # Now the item is announced with call_id+name.
            sse_data({"type": "response.output_item.added", "item": {"type": "function_call", "id": "itm_1", "call_id": "call_1", "name": "Bash"}}),
            sse_data({"type": "response.completed", "response": {"id": "resp_1", "model": "gpt-5.2", "created_at": 1710000000, "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}}),
            b"data: [DONE]\n\n",
        ]

        out_objs = []
        for c in chunks:
            for out in tr.feed(c):
                for ev, data in _iter_sse_frames(out):
                    if not data or data == "[DONE]":
                        continue
                    out_objs.append(json.loads(data))
        for out in tr.flush():
            for ev, data in _iter_sse_frames(out):
                if not data or data == "[DONE]":
                    continue
                out_objs.append(json.loads(data))

        # Collect all tool_call argument deltas for call_1.
        args = []
        for obj in out_objs:
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = (choices[0] or {}).get("delta") or {}
            for tc in delta.get("tool_calls") or []:
                if tc.get("id") == "call_1":
                    fn = tc.get("function") or {}
                    if isinstance(fn, dict) and fn.get("arguments"):
                        args.append(fn.get("arguments"))
        self.assertEqual("".join(args), "{\"cmd\":\"ls\"}")

