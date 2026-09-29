import unittest
from unittest.mock import patch

from src.agent import controller, routing
from src.agent.schemas import EvidenceGrade, QueryIntent, QueryPlan, VerificationResult, ClaimAssessment

ITEM = {"id": "1", "text": "Sở KH&CN phụ trách triển khai.", "score": 0.9,
        "payload": {"doc_id": 1, "so_ky_hieu": "1/TEST", "aspect_scores": {"0": 0.9}}}
VERIFIED = VerificationResult(answer="Sở KH&CN phụ trách [E1]", answer_complete=True, verified=True, confidence="cao",
                              claims=[ClaimAssessment(claim="Sở KH&CN phụ trách [E1]", status="supported", evidence_ids=["E1"])])


def fake_stream(*args, **kwargs):
    yield "Sở KH&CN "
    yield "phụ trách [E1]"


class RoutingTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(controller.store, "has_ready_documents", return_value=True),
            patch.object(controller.store, "log_rag_query"),
            patch.object(controller.llm, "chat_stream", side_effect=fake_stream),
            patch.object(controller, "verify_answer", return_value=VERIFIED),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

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

    def test_selective_strong_evidence_skips_llm_planner_and_grader(self):
        with patch.object(controller, "_retrieve", return_value=[dict(ITEM)]), \
             patch.object(controller, "create_plan") as planner, \
             patch.object(controller, "grade_evidence") as grader:
            events = list(controller.agent_events("Ai phụ trách triển khai?", routing_mode="selective"))
        planner.assert_not_called()
        grader.assert_not_called()
        types = [event["type"] for event in events]
        self.assertLess(types.index("sources"), types.index("token"))
        self.assertLess(types.index("token"), types.index("answer"))
        self.assertEqual("done", types[-1])
        result = events[-1]["result"]
        self.assertEqual("fast", result["route"])
        self.assertTrue(result["verified"])
        self.assertIn("[E1]", result["answer"])

    def test_always_mode_plans_once_and_grades(self):
        plan = QueryPlan(intent=QueryIntent.SEMANTIC_QA, sub_queries=["Ai phụ trách triển khai?"], planned_by="llm")
        with patch.object(controller, "_retrieve", return_value=[dict(ITEM)]) as retrieve, \
             patch.object(controller, "create_plan", return_value=plan) as planner, \
             patch.object(controller, "grade_evidence", return_value=EvidenceGrade(sufficient=True)) as grader:
            result = controller.run_agent("Ai phụ trách triển khai?", routing_mode="always")
        self.assertEqual(1, planner.call_count)
        self.assertEqual(1, grader.call_count)
        self.assertEqual(1, retrieve.call_count)
        self.assertEqual("agent", result.route)

    def test_weak_evidence_uses_grader_follow_up_query(self):
        weak = dict(ITEM, score=0.3)
        grades = [EvidenceGrade(sufficient=False, missing=["thời hạn"], next_queries=["thời hạn hoàn thành"]),
                  EvidenceGrade(sufficient=True)]
        with patch.object(controller, "_retrieve", return_value=[weak]) as retrieve, \
             patch.object(controller, "grade_evidence", side_effect=grades):
            result = controller.run_agent("Ai phụ trách triển khai?", routing_mode="selective")
        self.assertEqual(2, retrieve.call_count)
        self.assertEqual("thời hạn hoàn thành", retrieve.call_args_list[1].args[0])
        self.assertEqual(2, result.attempts)
        self.assertIsNotNone(result.fallback_reason)

    def test_unknown_reference_abstains_without_answer_model(self):
        with patch.object(controller, "_retrieve", return_value=[]), \
             patch.object(controller.store, "search_documents_by_number", return_value=[], create=True):
            result = controller.run_agent("Văn bản 976956/ZZ-KHONGTONTAI quy định những nhiệm vụ nào?")
        controller.llm.chat_stream.assert_not_called()
        self.assertEqual("Không tìm thấy thông tin trong kho dữ liệu.", result.answer)
        self.assertEqual([], result.sources)

    def test_pipeline_error_is_reported_not_raised(self):
        with patch.object(controller, "_retrieve", side_effect=RuntimeError("qdrant down")):
            result = controller.run_agent("Ai phụ trách triển khai?")
        self.assertIn("RuntimeError", result.error)
        self.assertIn("không phải kết luận", result.answer)


if __name__ == "__main__":
    unittest.main()
