import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from ai_proxy.moderation.smart.ai import ai_moderate
from ai_proxy.moderation.smart.profile import AIConfig, ModerationProfile


def _fake_response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content)
            )
        ]
    )


class _FakeCreate:
    def __init__(self, side_effects):
        self._side_effects = list(side_effects)
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        effect = self._side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class _FakeClient:
    def __init__(self, create_callable):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=create_callable)
        )


class TestSmartModerationAI(unittest.IsolatedAsyncioTestCase):
    def test_ai_config_parses_comma_separated_models(self) -> None:
        cfg = AIConfig(model=" gpt-4o-mini, , gpt-4.1-mini ,gpt-4.1 ")
        self.assertEqual(cfg.get_model_candidates(), ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"])
        self.assertEqual(AIConfig().max_retries, 2)
        self.assertEqual(AIConfig().timeout, 10)

    async def test_ai_moderate_retries_with_random_models_and_timeout(self) -> None:
        profile = ModerationProfile("__unit_test_missing_profile__")
        profile.config.ai.model = "model-a, model-b, model-c"
        profile.config.ai.timeout = 3
        profile.config.ai.max_retries = 2

        create = _FakeCreate([
            asyncio.TimeoutError(),
            RuntimeError("temporary failure"),
            _fake_response('{"violation": true, "category": "abuse", "reason": "blocked"}'),
        ])
        client = _FakeClient(create)

        with mock.patch("ai_proxy.moderation.smart.ai.get_or_create_openai_client", return_value=client):
            with mock.patch("ai_proxy.moderation.smart.ai.random.choice", side_effect=["model-b", "model-c", "model-a"]):
                result = await ai_moderate("test text", profile)

        self.assertTrue(result.violation)
        self.assertEqual(result.category, "abuse")
        self.assertEqual([call["model"] for call in create.calls], ["model-b", "model-c", "model-a"])
        self.assertEqual([call["timeout"] for call in create.calls], [3.0, 3.0, 3.0])


if __name__ == "__main__":
    unittest.main()
