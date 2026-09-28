import unittest
from unittest.mock import patch
from src.agent import controller, routing
from src.agent.schemas import Evidence, QueryPlan, QueryIntent, VerificationResult, ClaimAssessment


class RoutingTests(unittest.TestCase):
    def test_complex_question_never_fast(self):
        for intent in (QueryIntent.COMPARE, QueryIntent.LEGAL_STATUS, QueryIntent.AGGREGATE):
            self.assertFalse(routing.simple_question("Câu hỏi", QueryPlan(intent=intent)))
        self.assertFalse(routing.simple_question("Ai làm và khi nào?", QueryPlan()))

    def test_metadata_uses_only_exact_unambiguous_reference(self):
        plan = QueryPlan(intent=QueryIntent.EXACT_LOOKUP, document_refs=["1/TEST"])
        doc = {"id": 1, "trich_yeu": "Nội dung", "so_ky_hieu": "1/TEST"}
        with patch.object(routing.store, "search_documents_by_reference", return_value=[doc]):
            self.assertIsNotNone(routing.metadata_answer("Văn bản 1/TEST có nội dung chính là gì?", plan))
            self.assertIsNone(routing.metadata_answer("Văn bản 1/TEST có nội dung chính là gì và còn hiệu lực không?", plan))
        with patch.object(routing.store, "search_documents_by_reference", return_value=[doc, doc]):
            self.assertIsNone(routing.metadata_answer("Văn bản 1/TEST có nội dung chính là gì?", plan))

    def test_selective_skips_grader_only_after_complete_verification(self):
        item = {"id": "1", "text": "Bằng chứng", "score": 0.9, "payload": {"doc_id": 1}}
        verified = VerificationResult(answer="Đáp án [E1]", answer_complete=True,
                                      claims=[ClaimAssessment(claim="Đáp án", status="supported", evidence_ids=["E1"])])
        with patch.object(controller.store, "has_ready_documents", return_value=True), \
             patch.object(controller.store, "log_rag_query"), \
             patch.object(controller, "_retrieve", return_value=[item]), \
             patch.object(controller, "_compose_answer", return_value="draft"), \
             patch.object(controller, "verify_answer", return_value=verified), \
             patch.object(controller, "grade_evidence", return_value=(True, [])) as grader:
            fast = controller.run_agent("Ai phụ trách?", routing_mode="selective")
            self.assertEqual("verified_fast", fast.route)
            grader.assert_not_called()
            full = controller.run_agent("Ai phụ trách?", routing_mode="always")
            self.assertEqual("agent", full.route)
            self.assertEqual(1, grader.call_count)

    def test_incomplete_fast_result_escalates_without_retrieving_twice(self):
        item = {"id": "1", "text": "Bằng chứng", "score": 0.9, "payload": {"doc_id": 1}}
        with patch.object(controller.store, "has_ready_documents", return_value=True), \
             patch.object(controller.store, "log_rag_query"), \
             patch.object(controller, "_retrieve", return_value=[item]) as retrieve, \
             patch.object(controller, "_compose_answer", return_value="draft"), \
             patch.object(controller, "verify_answer", return_value=VerificationResult(answer="Chưa đủ")), \
             patch.object(controller, "grade_evidence", return_value=(True, [])):
            result = controller.run_agent("Ai phụ trách?", routing_mode="selective")
        self.assertEqual("agent", result.route)
        self.assertEqual("verification_incomplete", result.fallback_reason)
        self.assertEqual(1, retrieve.call_count)


if __name__ == "__main__":
    unittest.main()
