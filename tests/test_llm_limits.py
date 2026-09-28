import unittest
from types import SimpleNamespace
from unittest.mock import patch
from src.core import llm, config


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


if __name__ == "__main__":
    unittest.main()
