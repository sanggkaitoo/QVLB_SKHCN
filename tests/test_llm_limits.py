import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core import config, llm


def fake_provider():
    """Client OpenAI giả: _openai_client(provider).with_options(...) trả về cùng một đối tượng."""
    client = MagicMock()
    return patch.object(llm, "_openai_client", return_value=MagicMock(with_options=client)), client


def chunk(text=None, finish=None, usage=None):
    choices = [SimpleNamespace(finish_reason=finish, delta=SimpleNamespace(content=text))] if text or finish else []
    return SimpleNamespace(error=None, choices=choices, usage=usage)


class LLMLimitsTests(unittest.TestCase):
    def setUp(self):
        # Không đọc cấu hình admin trong database: vai trò lấy mặc định từ .env.
        settings = patch("src.core.app_settings.get", return_value=None)
        settings.start()
        self.addCleanup(settings.stop)

    def test_caps_output_and_accounts_usage(self):
        response = SimpleNamespace(error=None, choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK"))],
                                   usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1))
        patcher, client = fake_provider()
        with patcher:
            client.return_value.chat.completions.create.return_value = response
            with llm.track_usage() as usage:
                self.assertEqual("OK", llm.chat("system", "question"))
            self.assertEqual(config.LLM_MAX_OUTPUT_TOKENS, client.return_value.chat.completions.create.call_args.kwargs["max_tokens"])
            self.assertEqual(1, usage["calls"])
            self.assertEqual(2, usage["prompt_tokens"])

    def test_embedded_provider_error_is_not_a_none_type_failure(self):
        patcher, client = fake_provider()
        with patcher:
            client.return_value.chat.completions.create.return_value = SimpleNamespace(error={"code": 400, "message": "unsupported endpoint"}, choices=None)
            with self.assertRaisesRegex(RuntimeError, "Provider returned an error"):
                llm.chat("system", "question")

    def test_stream_yields_deltas_and_usage(self):
        stream = [chunk("Xin "), chunk("chào", finish="stop"), chunk(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2))]
        patcher, client = fake_provider()
        with patcher:
            client.return_value.chat.completions.create.return_value = iter(stream)
            with llm.track_usage() as usage:
                self.assertEqual(["Xin ", "chào"], list(llm.chat_stream("s", "u", models=["m1"])))
        self.assertEqual(5, usage["prompt_tokens"])
        self.assertTrue(client.return_value.chat.completions.create.call_args.kwargs["stream"])

    def test_stream_falls_back_only_before_first_token(self):
        patcher, client = fake_provider()
        with patcher:
            client.return_value.chat.completions.create.side_effect = [RuntimeError("down"), iter([chunk("OK", finish="stop")])]
            self.assertEqual(["OK"], list(llm.chat_stream("s", "u", models=["m1", "m2"])))

    def test_openai_direct_reasoning_model_params(self):
        response = SimpleNamespace(error=None, choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK"))], usage=None)
        patcher, client = fake_provider()
        with patcher:
            client.return_value.chat.completions.create.return_value = response
            llm.chat("s", "u", model="openai:gpt-5-mini", effort="low")
            kwargs = client.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual("gpt-5-mini", kwargs["model"])
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("max_tokens", kwargs)
        self.assertGreaterEqual(kwargs["max_completion_tokens"], 16000)
        self.assertEqual("low", kwargs["reasoning_effort"])

    def test_anthropic_route_uses_official_sdk_module(self):
        with patch.object(llm, "provider_key", return_value="sk-ant-test"), \
                patch("src.core.anthropic_provider.stream", return_value=iter(["Xin ", "chào"])) as stream:
            self.assertEqual(["Xin ", "chào"], list(llm.chat_stream("s", "u", models=["anthropic:claude-sonnet-5-5"])))
        self.assertEqual("claude-sonnet-5-5", stream.call_args.args[1])

    def test_user_selected_model_goes_first(self):
        with llm.request_scope(user_id=7, answer_model="openai:gpt-5-mini"):
            self.assertEqual("openai:gpt-5-mini", llm.answer_candidates()[0])
            self.assertEqual(7, llm.current_scope()["user_id"])
        self.assertEqual({}, llm.current_scope())

    def test_parse_json_tolerates_fences(self):
        self.assertEqual({"a": 1}, llm.parse_json('```json\n{"a": 1}\n```'))
        self.assertIsNone(llm.parse_json("không phải json"))


if __name__ == "__main__":
    unittest.main()
