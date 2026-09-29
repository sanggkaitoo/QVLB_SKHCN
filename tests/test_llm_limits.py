import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.core import config, llm


def chunk(text=None, finish=None, usage=None):
    choices = [SimpleNamespace(finish_reason=finish, delta=SimpleNamespace(content=text))] if text or finish else []
    return SimpleNamespace(error=None, choices=choices, usage=usage)


class LLMLimitsTests(unittest.TestCase):
    def test_caps_output_and_accounts_usage(self):
        response = SimpleNamespace(error=None, choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="OK"))],
                                   usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1))
        with patch.object(llm._client, "with_options") as client:
            client.return_value.chat.completions.create.return_value = response
            with llm.track_usage() as usage:
                self.assertEqual("OK", llm.chat("system", "question"))
            self.assertEqual(config.LLM_MAX_OUTPUT_TOKENS, client.return_value.chat.completions.create.call_args.kwargs["max_tokens"])
            self.assertEqual(1, usage["calls"])
            self.assertEqual(2, usage["prompt_tokens"])

    def test_embedded_provider_error_is_not_a_none_type_failure(self):
        with patch.object(llm._client, "with_options") as client:
            client.return_value.chat.completions.create.return_value = SimpleNamespace(error={"code": 400, "message": "unsupported endpoint"}, choices=None)
            with self.assertRaisesRegex(RuntimeError, "Provider returned an error"):
                llm.chat("system", "question")

    def test_stream_yields_deltas_and_usage(self):
        stream = [chunk("Xin "), chunk("chào", finish="stop"), chunk(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2))]
        with patch.object(llm._client, "with_options") as client:
            client.return_value.chat.completions.create.return_value = iter(stream)
            with llm.track_usage() as usage:
                self.assertEqual(["Xin ", "chào"], list(llm.chat_stream("s", "u", models=["m1"])))
        self.assertEqual(5, usage["prompt_tokens"])
        self.assertTrue(client.return_value.chat.completions.create.call_args.kwargs["stream"])

    def test_stream_falls_back_only_before_first_token(self):
        with patch.object(llm._client, "with_options") as client:
            client.return_value.chat.completions.create.side_effect = [RuntimeError("down"), iter([chunk("OK", finish="stop")])]
            self.assertEqual(["OK"], list(llm.chat_stream("s", "u", models=["m1", "m2"])))

    def test_parse_json_tolerates_fences(self):
        self.assertEqual({"a": 1}, llm.parse_json('```json\n{"a": 1}\n```'))
        self.assertIsNone(llm.parse_json("không phải json"))


if __name__ == "__main__":
    unittest.main()
