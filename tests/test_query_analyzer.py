import unittest

from src.agent.query_analyzer import detect_date_range, detect_filters, detect_intent, partial_reference_numbers
from src.agent.schemas import QueryIntent
from src.services.document_fields import extract_document_refs, normalize_filters


class QueryAnalyzerTests(unittest.TestCase):
    def test_exact_reference_routes_to_exact_lookup(self):
        query = "Văn bản 230/QĐ-SKHCN quy định gì?"
        self.assertEqual(QueryIntent.EXACT_LOOKUP, detect_intent(query))
        self.assertEqual(["230/QD-SKHCN"], extract_document_refs(query))

    def test_party_style_and_dates_are_not_confused(self):
        self.assertEqual(["57-NQ/TW"], extract_document_refs("Nghị quyết số 57-NQ/TW ngày 22/12/2024"))
        self.assertEqual([], extract_document_refs("báo cáo tháng 12/2024 và 3/4 số xã"))

    def test_detects_aggregate(self):
        self.assertEqual(QueryIntent.AGGREGATE, detect_intent("Tổng số báo cáo năm 2026 là bao nhiêu?"))
        self.assertEqual(QueryIntent.AGGREGATE, detect_intent("Có bao nhiêu công văn đến trong quý 3/2026?"))
        self.assertEqual(QueryIntent.AGGREGATE, detect_intent("Tổng kinh phí các dự án trong các tờ trình"))

    def test_single_document_question_is_not_aggregate(self):
        self.assertNotEqual(QueryIntent.AGGREGATE, detect_intent("Thời hạn nộp báo cáo là bao nhiêu ngày?"))
        self.assertEqual(QueryIntent.EXACT_LOOKUP, detect_intent("Tổng kinh phí của 215/KH-UBND là bao nhiêu?"))

    def test_detects_filters(self):
        filters = detect_filters("Tìm báo cáo văn bản đến năm 2026")
        self.assertEqual(["bao_cao"], filters["loai_vb"])
        self.assertEqual(["den"], filters["huong"])
        self.assertEqual("2026-01-01", filters["date_from"])

    def test_date_ranges(self):
        self.assertEqual({"date_from": "2026-07-01", "date_to": "2026-09-30"}, detect_date_range("quý III/2026"))
        self.assertEqual({"date_from": "2026-02-01", "date_to": "2026-02-28"}, detect_date_range("tháng 2 năm 2026"))
        self.assertEqual({"date_from": "2026-01-01", "date_to": "2026-06-30"}, detect_date_range("6 tháng đầu năm 2026"))
        self.assertEqual({"date_from": "2026-03-05", "date_to": "2026-04-10"},
                         detect_date_range("từ ngày 5/3/2026 đến ngày 10/4/2026"))

    def test_partial_reference_numbers(self):
        self.assertEqual(["2072"], partial_reference_numbers("Công văn 2072 nói gì?"))
        self.assertEqual([], partial_reference_numbers("Công văn 2072/SKHCN-QLCN nói gì?"))

    def test_normalize_filters(self):
        filters = normalize_filters({"loai_vb": "bao_cao,ke_hoach,<script>", "huong": "den,xyz",
                                     "date_from": "2026-13-40", "linh_vuc": ["cds"], "co_quan_ban_hanh": "  UBND  tỉnh "})
        self.assertEqual({"loai_vb": ["bao_cao", "ke_hoach"], "huong": ["den"], "linh_vuc": ["cds"],
                          "co_quan_ban_hanh": "UBND tỉnh"}, filters)


if __name__ == "__main__":
    unittest.main()
