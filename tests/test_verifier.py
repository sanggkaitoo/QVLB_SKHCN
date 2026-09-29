import unittest
from unittest.mock import patch

from src.agent.schemas import Evidence
from src.agent.verifier import UNVERIFIED_ANSWER, evidence_is_sufficient, split_segments, verify_answer

EVIDENCE = [Evidence(evidence_id="E1", text="Hạn nộp là 30/6.", score=0.9),
            Evidence(evidence_id="E2", text="Sở KH&CN chủ trì.", score=0.8)]


class VerifierTests(unittest.TestCase):
    def test_exact_evidence_is_sufficient(self):
        evidence = [Evidence(evidence_id="E1", text="Trích yếu văn bản",
                             metadata={"retrieval_tool": "exact_document_search"}, score=0.9)]
        self.assertTrue(evidence_is_sufficient(evidence, exact_lookup=True))

    def test_semantic_requires_minimum_evidence(self):
        one = [Evidence(evidence_id="E1", text="Một nguồn", score=0.8)]
        self.assertFalse(evidence_is_sufficient(one, exact_lookup=False))

    def test_segments_keep_markdown_lines(self):
        segments = split_segments("**Kết quả:**\n- Hạn nộp là 30/6 [E1]. Sở chủ trì [E2].\n- Khác.")
        self.assertEqual(["**Kết quả:**", "- Hạn nộp là 30/6 [E1].", "Sở chủ trì [E2].", "- Khác."],
                         [text for _, text in segments])

    def test_unsupported_segment_is_removed_and_format_kept(self):
        draft = "Tóm tắt:\n- Hạn nộp là 30/6 [E1].\n- Kinh phí 5 tỷ đồng [E2]."
        verdict = {"segments": [{"id": "S1", "status": "not_a_claim"}, {"id": "S2", "status": "supported"},
                                {"id": "S3", "status": "unsupported"}], "answer_complete": False}
        with patch("src.agent.verifier.llm.extract_json", return_value=verdict):
            result = verify_answer("Hạn nộp và kinh phí?", draft, EVIDENCE)
        self.assertIn("- Hạn nộp là 30/6 [E1].", result.answer)
        self.assertNotIn("5 tỷ", result.answer)
        self.assertTrue(result.changed)
        self.assertFalse(result.answer_complete)

    def test_invalid_or_missing_citation_is_not_supported(self):
        draft = "Hạn nộp là 30/6 [E9]. Sở KH&CN chủ trì."
        verdict = {"segments": [{"id": "S1", "status": "supported"}, {"id": "S2", "status": "supported"}]}
        with patch("src.agent.verifier.llm.extract_json", return_value=verdict):
            result = verify_answer("?", draft, EVIDENCE)
        self.assertEqual(UNVERIFIED_ANSWER, result.answer)

    def test_list_items_share_block_citation(self):
        draft = "Các văn bản gồm:\n\n- Quyết định 1686/QĐ-UBND\n- Kế hoạch 309/KH-UBND\n\nNguồn: Báo cáo 431 [E2]."
        verdict = {"segments": [{"id": f"S{i}", "status": "supported"} for i in range(1, 5)], "answer_complete": True}
        with patch("src.agent.verifier.llm.extract_json", return_value=verdict):
            result = verify_answer("?", draft, EVIDENCE)
        self.assertIn("1686/QĐ-UBND", result.answer)
        self.assertIn("309/KH-UBND", result.answer)
        self.assertFalse(result.changed)

    def test_uncited_paragraph_is_still_removed(self):
        draft = "Hạn nộp là 30/6 [E1].\n\nKinh phí là 5 tỷ đồng."
        verdict = {"segments": [{"id": "S1", "status": "supported"}, {"id": "S2", "status": "supported"}]}
        with patch("src.agent.verifier.llm.extract_json", return_value=verdict):
            result = verify_answer("?", draft, EVIDENCE)
        self.assertNotIn("5 tỷ", result.answer)

    def test_verifier_never_returns_unverified_draft(self):
        with patch("src.agent.verifier.llm.extract_json", side_effect=RuntimeError("offline")):
            result = verify_answer("question", "invented fact [E1]", EVIDENCE)
        self.assertNotIn("invented fact", result.answer)
        self.assertEqual("thap", result.confidence)
        self.assertFalse(result.verified)


if __name__ == "__main__":
    unittest.main()
