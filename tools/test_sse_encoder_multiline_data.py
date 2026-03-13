import unittest

from openai._streaming import SSEDecoder

from ai_proxy.proxy.stream_transformer import _encode_sse


class TestSSEEncoderMultilineData(unittest.TestCase):
    def test_multiline_data_is_encoded_as_multiple_data_lines(self) -> None:
        payload = "{\"a\":1}\n{\"b\":2}"
        frame = _encode_sse(payload, event="test.event")

        dec = SSEDecoder()
        events = list(dec.iter_bytes(iter([frame])))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.event, "test.event")
        # SSEDecoder concatenates multi-line data with newlines.
        self.assertEqual(ev.data, payload)

