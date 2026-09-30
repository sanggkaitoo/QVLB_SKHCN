import unittest

from src.core import anthropic_provider as ap


class AnthropicRequestTests(unittest.TestCase):
    def test_fallback_and_effort_for_new_models(self):
        params = ap._request("claude-opus-5-5", "sys", "hỏi", 600, "low")
        self.assertEqual("default", params["fallbacks"])
        self.assertEqual([ap.FALLBACK_BETA], params["betas"])
        self.assertEqual({"effort": "low"}, params["output_config"])
        self.assertGreaterEqual(params["max_tokens"], 16000)  # chừa chỗ cho phần suy nghĩ
        self.assertNotIn("temperature", params)

    def test_older_models_skip_unsupported_options(self):
        params = ap._request("claude-haiku-4-5", "sys", "hỏi", 600, "low")
        self.assertNotIn("fallbacks", params)
        self.assertNotIn("output_config", params)
        self.assertEqual(600, params["max_tokens"])

    def test_image_content_passes_through(self):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
                   {"type": "text", "text": "OCR"}]
        self.assertIs(content, ap._request("claude-sonnet-5-5", "sys", content, 100, None)["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
