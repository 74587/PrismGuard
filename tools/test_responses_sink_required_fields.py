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


class TestResponsesSinkRequiredFields(unittest.TestCase):
    def test_created_response_has_required_nullable_fields(self) -> None:
        tr = create_stream_transformer("openai_chat", "openai_responses")
        self.assertIsNotNone(tr)

        chunk = {
            "id": "chatcmpl_x",
            "object": "chat.completion.chunk",
            "created": 1710000000,
            "model": "gpt-5.2",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        out_objs = []
        for out in tr.feed(("data: " + json.dumps(chunk, separators=(",", ":")) + "\n\n").encode("utf-8")):
            for ev, data in _iter_sse_frames(out):
                if not data or data == "[DONE]":
                    continue
                out_objs.append(json.loads(data))

        created = next((o for o in out_objs if o.get("type") == "response.created"), None)
        self.assertIsNotNone(created)
        resp = created.get("response") or {}
        self.assertIn("error", resp)
        self.assertIn("incomplete_details", resp)

