import unittest

from src.agent.query_analyzer import detect_filters, detect_intent
from src.agent.schemas import QueryIntent
from src.services.retrieval_srv import extract_document_refs


class QueryAnalyzerTests(unittest.TestCase):
    def test_exact_reference_routes_to_exact_lookup(self):
        query = "Văn bản 230/QĐ-SKHCN quy định gì?"
        self.assertEqual(QueryIntent.EXACT_LOOKUP, detect_intent(query))
        self.assertEqual(["230/QD-SKHCN"], extract_document_refs(query))

    def test_detects_aggregate(self):
        self.assertEqual(QueryIntent.AGGREGATE, detect_intent("Tổng số báo cáo năm 2026 là bao nhiêu?"))

    def test_detects_filters(self):
        filters = detect_filters("Tìm báo cáo văn bản đến năm 2026")
        self.assertEqual("bao_cao", filters["loai_vb"])
        self.assertEqual("den", filters["huong"])
        self.assertEqual("2026-01-01", filters["date_from"])


if __name__ == "__main__":
    unittest.main()
