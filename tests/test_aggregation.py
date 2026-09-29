import unittest

from src.services import aggregate_srv
from src.services.numbers import format_number, normalize_quantity, parse_number
from src.services.relations import extract_relations


def fact(doc_id, value_text, unit, quote, chunk_text=None, **raw):
    document = {"id": doc_id, "so_ky_hieu": f"{doc_id}/BC", "ngay_ban_hanh": "2026-03-31"}
    chunks = [{"text": chunk_text or quote, "payload": {"file_name": "a.pdf", "file_role": "chinh"}}]
    payload = {"chunk": "C1", "label": raw.pop("label", "dòng"), "value_text": value_text, "unit_text": unit,
               "quote": quote, "confidence": raw.pop("confidence", 0.9), **raw}
    return aggregate_srv._prepare_fact(payload, document, chunks)


class NumberTests(unittest.TestCase):
    def test_vietnamese_separators_and_multipliers(self):
        self.assertEqual(1234567.0, normalize_quantity("1.234.567", "đồng")["value"])
        self.assertEqual(1_234_500_000.0, normalize_quantity("1.234,5", "triệu đồng")["value"])
        self.assertEqual(2_500_000_000.0, normalize_quantity("2,5 tỷ đồng")["value"])
        self.assertEqual({"value": 15.0, "unit": "%", "kind": "percent", "ambiguous": False}, normalize_quantity("15%"))
        self.assertEqual("buổi", normalize_quantity("12 buổi")["unit"])
        self.assertTrue(parse_number("1.200")[1])  # thousands or decimal: flagged ambiguous
        self.assertEqual("1.234.567 đồng", format_number(1234567.0, "đồng"))


class AggregationTests(unittest.TestCase):
    def test_quote_must_come_from_source_chunk(self):
        grounded = fact(1, "12", "buổi", "đã tổ chức 12 buổi", "Trong quý, đã tổ chức 12 buổi tập huấn.")
        invented = fact(2, "15", "buổi", "đã tổ chức 15 buổi", "Trong quý, đã tổ chức 12 buổi tập huấn.")
        self.assertEqual([], grounded["issues"])
        self.assertIn("trich_dan_khong_khop_nguon", invented["issues"])

    def test_total_row_replaces_details_and_duplicates_are_reviewed(self):
        plan = aggregate_srv.AggregationPlan(metric="số buổi", operation="sum")
        facts = [
            fact(1, "5", "buổi", "xã A: 5 buổi"),
            fact(1, "7", "buổi", "xã B: 7 buổi"),
            fact(1, "12", "buổi", "Tổng cộng: 12 buổi", is_total=True),
            fact(2, "12", "buổi", "Tổng cộng: 12 buổi", is_total=True),
            fact(3, "3", "buổi", "tổ chức 3 buổi"),
            fact(3, "4", "lượt", "4 lượt người"),
        ]
        warnings = []
        included, review = aggregate_srv._select_facts(plan, facts, warnings)
        self.assertEqual(15.0, aggregate_srv._reduce("sum", included))
        reasons = sorted(item["review_reason"] for item in review)
        self.assertEqual(["chi_tiet_da_co_dong_tong", "chi_tiet_da_co_dong_tong", "khac_don_vi", "trung_lap_giua_van_ban"], reasons)
        self.assertEqual([], warnings)

    def test_status_mismatch_and_low_confidence_go_to_review(self):
        plan = aggregate_srv.AggregationPlan(metric="số buổi", operation="sum", status="thuc_hien")
        facts = [fact(1, "10", "buổi", "dự kiến 10 buổi", status="ke_hoach"),
                 fact(2, "6", "buổi", "đã tổ chức 6 buổi", status="thuc_hien"),
                 fact(3, "8", "buổi", "khoảng 8 buổi", status="thuc_hien", confidence=0.3)]
        included, review = aggregate_srv._select_facts(plan, facts, [])
        self.assertEqual([6.0], [item["value"] for item in included])
        self.assertEqual({"khac_trang_thai_ke_hoach", "do_tin_cay_thap"}, {item["review_reason"] for item in review})

    def test_detail_total_mismatch_is_warned(self):
        plan = aggregate_srv.AggregationPlan(metric="kinh phí", operation="sum")
        facts = [fact(1, "5", "triệu đồng", "A: 5 triệu đồng"), fact(1, "20", "triệu đồng", "Tổng: 20 triệu đồng", is_total=True)]
        warnings = []
        included, _ = aggregate_srv._select_facts(plan, facts, warnings)
        self.assertEqual(20_000_000.0, aggregate_srv._reduce("sum", included))
        self.assertEqual(1, len(warnings))

    def test_group_keys_and_operations(self):
        facts = [{"value": 2.0, "label": "A", "ngay_ban_hanh": "2026-03-01"}, {"value": 4.0, "label": "a", "ngay_ban_hanh": "2026-04-01"}]
        self.assertEqual(3.0, aggregate_srv._reduce("avg", facts))
        self.assertEqual(1.0, aggregate_srv._reduce("distinct_count", facts))
        self.assertEqual("2026-03", aggregate_srv._group_key(facts[0], "thang"))


class AggregationPlanTests(unittest.TestCase):
    def test_empty_request_filters_do_not_erase_rule_filters(self):
        plan = aggregate_srv.plan_aggregation("Có bao nhiêu công văn đi trong năm 2026?",
                                              {"loai_vb": None, "huong": None, "date_from": None, "date_to": None})
        self.assertEqual("count_documents", plan.operation)
        self.assertEqual({"loai_vb": ["cong_van"], "huong": ["di"], "date_from": "2026-01-01", "date_to": "2026-12-31"},
                         plan.filters)
        self.assertEqual("", plan.keywords)

    def test_status_comes_from_question_wording(self):
        self.assertEqual("bat_ky", aggregate_srv.rule_status("Tổng kinh phí được nêu trong các văn bản"))
        self.assertEqual("thuc_hien", aggregate_srv.rule_status("Tổng số buổi đã tổ chức"))
        self.assertEqual("chuyển đổi số", aggregate_srv.document_topic("Có bao nhiêu kế hoạch về chuyển đổi số năm 2026?"))


class RelationTests(unittest.TestCase):
    def test_extracts_typed_relations_and_skips_self(self):
        text = ("Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05/3/2020;\n"
                "Thực hiện Kế hoạch số 01-KH/TU của Tỉnh ủy.\n"
                "Quyết định này thay thế Quyết định số 12/QĐ-UBND. Văn bản 215/QĐ-UBND có hiệu lực.")
        relations = {(item["relation_type"], item["normalized_target_ref"]) for item in extract_relations(text, "215/QĐ-UBND")}
        self.assertEqual({("can_cu", "30/2020/ND-CP"), ("lien_quan", "01-KH/TU"), ("thay_the", "12/QD-UBND")}, relations)


if __name__ == "__main__":
    unittest.main()
