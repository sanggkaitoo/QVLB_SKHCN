import datetime as dt
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import docx
import httpx

from src.check import content_ai, content_logic, content_review, legal_registry, references

DRAFT = dt.date(2026, 9, 30)


def _paras(*texts, role="body"):
    return [(number, text, role) for number, text in enumerate(texts, 1)]


class ExtractReferencesTest(unittest.TestCase):
    def test_multiple_references_per_line_and_kinds(self):
        refs = references.extract(_paras(
            "Căn cứ Luật Khoa học, công nghệ và đổi mới sáng tạo số 93/2025/QH15 ngày 27 tháng 6 năm 2025;",
            "Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05/3/2020 của Chính phủ về công tác văn thư và "
            "Nghị quyết số 57-NQ/TW ngày 22/12/2024 của Bộ Chính trị;",
            "Thực hiện Công văn số 215/UBND-NC ngày 10/9/2026 của UBND tỉnh Lào Cai về việc báo cáo tiến độ, "
            "Sở đề nghị các đơn vị báo cáo.",
        ))
        by_id = {ref.identifier: ref for ref in refs}
        self.assertEqual(set(by_id), {"93/2025/QH15", "30/2020/NĐ-CP", "57-NQ/TW", "215/UBND-NC"})
        self.assertEqual((by_id["93/2025/QH15"].kind, by_id["93/2025/QH15"].rank), ("qppl", 1))
        self.assertEqual(by_id["93/2025/QH15"].title, "Khoa học, công nghệ và đổi mới sáng tạo")
        self.assertEqual(by_id["30/2020/NĐ-CP"].date, dt.date(2020, 3, 5))
        self.assertEqual(by_id["30/2020/NĐ-CP"].issuer, "Chính phủ")
        self.assertEqual(by_id["30/2020/NĐ-CP"].title, "công tác văn thư")
        self.assertEqual(by_id["57-NQ/TW"].kind, "dang")
        self.assertEqual(by_id["215/UBND-NC"].kind, "hanh_chinh")
        self.assertFalse(by_id["215/UBND-NC"].in_basis)
        self.assertEqual(by_id["215/UBND-NC"].issuer, "UBND tỉnh Lào Cai")

    def test_named_law_and_merge_with_numbered_mention(self):
        refs = references.extract(_paras(
            "Căn cứ Luật Tổ chức chính quyền địa phương ngày 16 tháng 6 năm 2025;",
            "Căn cứ Luật Tần số vô tuyến điện ngày 23 tháng 11 năm 2009;",
            "Theo Luật Tần số vô tuyến điện số 42/2009/QH12, đơn vị phải cấp phép.",
        ))
        labels = [ref.label for ref in refs]
        self.assertIn("Luật Tổ chức chính quyền địa phương", labels)
        merged = [ref for ref in refs if ref.identifier == "42/2009/QH12"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].date, dt.date(2009, 11, 23))
        self.assertEqual(merged[0].mentions, [2, 3])
        self.assertEqual(len(refs), 2)

    def test_lowercase_luat_in_prose_is_not_a_reference(self):
        refs = references.extract(_paras("Thực hiện đúng quy định của pháp luật về đất đai ngày 01/01/2025."))
        self.assertEqual(refs, [])

    def test_inconsistent_dates_between_mentions(self):
        refs = references.extract(_paras(
            "Thực hiện Kế hoạch số 368/KH-UBND ngày 31/7/2026 của UBND tỉnh;",
            "Theo Kế hoạch số 368/KH-UBND ngày 13/7/2026, các sở báo cáo.",
        ))
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].checks[0]["rule"], "consistency")
        self.assertEqual(refs[0].checks[0]["status"], "fail")

    def test_dotted_government_resolution_number(self):
        refs = references.extract(_paras("Căn cứ Nghị quyết số 66.18/2026/NQ-CP ngày 18/5/2026 của Chính phủ;"))
        self.assertEqual([(ref.identifier, ref.kind, ref.rank) for ref in refs], [("66.18/2026/NQ-CP", "qppl", 3)])

    def test_hdnd_hyphen_number_is_not_party(self):
        self.assertEqual(references.classify("53-NQ/HĐND", "Nghị quyết")[0], "hanh_chinh")
        self.assertEqual(references.classify("12/2025/QĐ-UBND", "Quyết định")[:2], ("qppl", "local"))
        self.assertEqual(references.classify("1132/QĐ-TTg", "Quyết định")[0], "hanh_chinh")


class BasisBlockTest(unittest.TestCase):
    def test_order_and_punctuation(self):
        paragraphs = _paras(
            "Căn cứ Thông tư số 09/2024/TT-BKHCN ngày 27/12/2024 của Bộ trưởng Bộ Khoa học và Công nghệ;",
            "Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05/3/2020 của Chính phủ về công tác văn thư",
            "Căn cứ Quyết định số 75/QĐ-SKHCN ngày 17/3/2026 của Giám đốc Sở;",
            "Nội dung chính của văn bản.",
        )
        refs = references.extract(paragraphs)
        results = references.check_basis_block(refs, paragraphs)
        rules = [(item["rule"], item["paragraph"]) for item in results]
        self.assertIn(("order", 2), rules)          # Nghị định đứng sau Thông tư
        self.assertIn(("punct", 2), rules)          # thiếu ";"
        self.assertIn(("punct", 3), rules)          # dòng căn cứ cuối kết thúc bằng ";"

    def test_valid_block_passes(self):
        paragraphs = _paras(
            "Căn cứ Luật Tổ chức chính quyền địa phương ngày 16 tháng 6 năm 2025;",
            "Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05/3/2020 của Chính phủ về công tác văn thư;",
            "Căn cứ Thông tư số 09/2024/TT-BKHCN ngày 27/12/2024 của Bộ Khoa học và Công nghệ.",
        )
        results = references.check_basis_block(references.extract(paragraphs), paragraphs)
        self.assertEqual([item["status"] for item in results], ["pass"])

    def test_basis_line_without_date(self):
        paragraphs = _paras("Căn cứ Kế hoạch số 52/KH-UBND của UBND tỉnh Lào Cai.")
        results = references.check_basis_block(references.extract(paragraphs), paragraphs)
        self.assertIn("basis_date", [item["rule"] for item in results])


class KhoComparisonTest(unittest.TestCase):
    def _ref(self, text):
        return references.extract(_paras(text))[0]

    def test_date_mismatch_and_replacement(self):
        ref = self._ref("Thực hiện Kế hoạch số 368/KH-UBND ngày 13/7/2026 của Ủy ban nhân dân tỉnh Lào Cai về chuyển đổi số")
        row = {"id": 7, "so_ky_hieu": "368/KH-UBND", "ngay_ban_hanh": dt.date(2026, 7, 31),
               "co_quan_ban_hanh": "UBND tỉnh Lào Cai", "trich_yeu": "Kế hoạch hành động chuyển đổi số",
               "source_url": "https://qlvb.example/368", "huong": "den", "loai_vb": "KH"}
        relation = {"source_document_id": 9, "target_document_id": 7, "relation_type": "thay_the",
                    "source_so_ky_hieu": "400/KH-UBND", "verified": True}
        with mock.patch.object(references.store, "search_documents_by_reference", return_value=[row]), \
                mock.patch.object(references.store, "get_document_relations", return_value=[relation]):
            references.check_kho(ref)
        statuses = {check["rule"]: check["status"] for check in ref.checks}
        self.assertEqual(statuses["date"], "fail")
        self.assertEqual(statuses["relation"], "fail")
        self.assertNotIn("issuer", statuses)        # "UBND" và "Ủy ban nhân dân" là một
        self.assertEqual(ref.kho["ngay_ban_hanh"], "31/07/2026")

    def test_typo_suggestion_only_for_same_date(self):
        ref = self._ref("Thực hiện Công văn số 2052/SKHCN-VP ngày 06/8/2026 của Sở")
        similar = [{"so_ky_hieu": "2025/SKHCN-VP", "ngay_ban_hanh": dt.date(2026, 8, 6)},
                   {"so_ky_hieu": "2051/SKHCN-VP", "ngay_ban_hanh": dt.date(2026, 1, 2)}]
        with mock.patch.object(references.store, "search_documents_by_reference", return_value=[]), \
                mock.patch.object(references.store, "find_doc_by_soky", return_value=similar):
            references.check_kho(ref)
        self.assertEqual(ref.checks[0]["status"], "warn")
        self.assertIn("2025/SKHCN-VP", ref.checks[0]["hint"])
        self.assertNotIn("2051", ref.checks[0]["hint"])

    def test_central_legal_document_not_reported_missing_from_kho(self):
        ref = self._ref("Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05/3/2020 của Chính phủ;")
        with mock.patch.object(references.store, "search_documents_by_reference", return_value=[]), \
                mock.patch.object(references.store, "find_doc_by_soky", return_value=[]):
            references.check_kho(ref)
        self.assertEqual(ref.checks, [])

    def test_future_reference_and_year_mismatch(self):
        ref = self._ref("Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05/3/2021 của Chính phủ;")
        references.check_structure(ref, DRAFT)
        self.assertEqual(ref.checks[0]["rule"], "year_date")
        ref = self._ref("Thực hiện Công văn số 10/UBND-NC ngày 05/10/2026 của UBND tỉnh")
        references.check_structure(ref, DRAFT)
        self.assertEqual(ref.checks[0]["rule"], "future")


class LogicTest(unittest.TestCase):
    def rules(self, text, dated=True):
        return [(item["rule"], item["status"]) for item in content_logic.check(_paras(text), DRAFT, dated)]

    def test_dates(self):
        self.assertEqual(self.rules("Tổ chức vào ngày 30/02/2026."), [("invalid_date", "fail")])
        self.assertEqual(self.rules("Họp vào 8 giờ, thứ Hai, ngày 05/10/2026."), [])
        self.assertEqual(self.rules("Họp ngày 06 tháng 10 năm 2026 (thứ Hai)."), [("weekday", "fail")])
        self.assertEqual(self.rules("Gửi báo cáo về Sở trước ngày 25/9/2026."), [("deadline_past", "warn")])
        self.assertEqual(self.rules("Gửi báo cáo về Sở trước ngày 25/9/2026.", dated=False), [("deadline_past", "info")])
        self.assertEqual(self.rules("Thời gian từ ngày 10/10/2026 đến ngày 01/10/2026."), [("range", "fail")])
        self.assertEqual(self.rules("Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05 tháng 3 năm 2020."), [])

    def test_number_words(self):
        self.assertEqual(content_logic.words_to_number("Một tỷ hai trăm linh năm triệu đồng chẵn"), 1_205_000_000)
        self.assertEqual(content_logic.words_to_number("hai mươi lăm"), 25)
        self.assertEqual(content_logic.words_to_number("ba mươi tư người"), 34)
        self.assertIsNone(content_logic.words_to_number("sau đây gọi tắt là Kế hoạch"))
        self.assertIsNone(content_logic.words_to_number("không thành lập Hội đồng"))
        self.assertEqual(self.rules("Kinh phí 1.200.000 đồng (Một triệu hai trăm nghìn đồng), cử 02 (hai) người."), [])
        self.assertEqual(self.rules("Thời gian 05 (bốn) ngày làm việc."), [("number_words", "fail")])

    def test_internal_references(self):
        paragraphs = [(1, "Điều 1. Ban hành Quy chế", "body"), (2, "Điều 2. Hiệu lực thi hành", "body"),
                      (3, "Điều 4. Chánh Văn phòng chịu trách nhiệm; thực hiện theo Điều 5 Quyết định này, "
                          "chi tiết tại Phụ lục 02 kèm theo; khoản 1 Điều 9 Luật Khoa học.", "body"),
                      (4, "PHỤ LỤC I", "phu_luc"), (5, "Theo Phụ lục II Nghị định số 30/2020/NĐ-CP.", "body")]
        rules = {(item["rule"], item["current"]) for item in content_logic.check_internal(paragraphs)}
        self.assertIn(("article_seq", "Điều 4"), rules)
        self.assertIn(("article_ref", "Điều 5"), rules)
        self.assertIn(("appendix", "Không thấy Phụ lục 02"), rules)
        self.assertEqual(len(rules), 3)      # Điều 9 Luật…, Phụ lục II Nghị định… là của văn bản khác

    def test_roman_heading_gap(self):
        paragraphs = [(1, "I. MỤC ĐÍCH", "heading"), (2, "II. NỘI DUNG", "heading"), (3, "IV. TỔ CHỨC THỰC HIỆN", "heading")]
        issues = content_logic.check_internal(paragraphs)
        self.assertEqual([item["rule"] for item in issues], ["roman_seq"])


class LegalRegistryTest(unittest.TestCase):
    PAGE = ('<html><script type="application/ld+json">{"@type":"Legislation","name":"Quyết định số \'12/2025/QĐ-UBND '
            'Ban hành Quy định chức năng của Sở Khoa học và Công nghệ","legislationIdentifier":"\'12/2025/QĐ-UBND",'
            '"legislationType":"Quyết định","legislationDate":"2025-07-01T00:00:00","legislationLegalForce":"InForce",'
            '"legislationPassedBy":{"name":"UBND Tỉnh Lào Cai"}}</script></html>')

    def test_normalize_identifier_strips_stray_quote(self):
        self.assertEqual(legal_registry.normalize_identifier("'12/2025/QĐ-UBND"), "12/2025/QD-UBND")

    def test_parse_and_lookup_with_mock_transport(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=self.PAGE)))
        saved = []
        with mock.patch.object(legal_registry, "_cached", return_value=[]), \
                mock.patch.object(legal_registry, "_candidates", return_value=["https://vbpl.vn/a--1"]), \
                mock.patch.object(legal_registry, "_save", side_effect=lambda url, data: saved.append(data) or {
                    "url": url, "identifier": data["legislationIdentifier"], "doc_type": data["legislationType"],
                    "issued": data["legislationDate"][:10], "legal_force": data["legislationLegalForce"],
                    "issuer": data["legislationPassedBy"]["name"], "name": data["name"]}), \
                mock.patch.object(legal_registry.config, "LEGAL_FETCH_INTERVAL", 0):
            result = legal_registry.lookup("12/2025/QĐ-UBND", client=client)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["identifier"], "12/2025/QĐ-UBND")
        self.assertEqual(result["force_level"], "pass")
        self.assertEqual(saved[0]["legislationIdentifier"], "12/2025/QĐ-UBND")

    def test_best_record_by_date_then_title(self):
        records = [{"url": "a", "issued": dt.date(2025, 1, 24), "name": "Quy định quản lý nhà ở công vụ"},
                   {"url": "b", "issued": dt.date(2025, 7, 1), "name": "Quy định chức năng Sở Khoa học và Công nghệ"}]
        self.assertEqual(legal_registry._best(records, "", dt.date(2025, 7, 1))["url"], "b")
        self.assertEqual(legal_registry._best(records, "nhà ở công vụ", None)["url"], "a")

    def test_reference_legal_checks(self):
        ref = references.extract(_paras("Căn cứ Nghị định số 13/2023/NĐ-CP ngày 17/4/2023 của Chính phủ;"))[0]
        found = {"status": "found", "url": "https://vbpl.vn/x", "identifier": "13/2023/NĐ-CP", "doc_type": "Nghị định",
                 "issuer": "Chính phủ", "name": "Nghị định 13/2023/NĐ-CP", "issued": "2023-04-17",
                 "legal_force": "NotInForce", "force_level": "fail", "force_label": "Hết hiệu lực", "same_number": 1}
        with mock.patch.object(legal_registry, "lookup", return_value=found):
            references.check_legal(ref, client=None)
        statuses = {check["rule"]: check["status"] for check in ref.checks}
        self.assertEqual(statuses, {"force": "fail", "date": "pass"})

    def test_recent_central_document_missing_is_info(self):
        ref = references.extract(_paras(f"Căn cứ Nghị định số 15/{dt.date.today().year}/NĐ-CP của Chính phủ;"))[0]
        with mock.patch.object(legal_registry, "lookup", return_value={"status": "not_found"}):
            references.check_legal(ref, client=None)
        self.assertEqual(ref.checks[0]["status"], "info")


class AiFilterTest(unittest.TestCase):
    def setUp(self):
        self.paragraph = SimpleNamespace(index=12, text="Đề nghị các đơn vị gửi báo cáo về Sở trước ngày 20/9/2026 theo Kế hoạch số 368/KH-UBND.",
                                         excerpt=lambda limit=90: "Đề nghị các đơn vị gửi báo cáo…")
        self.ref = SimpleNamespace(label="Kế hoạch 368/KH-UBND",
                                   kho={"so_ky_hieu": "368/KH-UBND", "url": None, "id": 1})

    def test_grounded_requires_verbatim_claim_and_evidence(self):
        raw = {"ref": self.ref, "source": "Các sở, ngành gửi báo cáo về Sở Khoa học và Công nghệ trước ngày 15/9/2026.",
               "findings": [
                   {"paragraph_id": "p12", "claim": "trước ngày 20/9/2026", "verdict": "sai", "confidence": 0.9,
                    "evidence": "gửi báo cáo về Sở Khoa học và Công nghệ trước ngày 15/9/2026", "explanation": "Sai hạn"},
                   {"paragraph_id": "p12", "claim": "trước ngày 20/9/2026 theo", "verdict": "sai", "confidence": 0.9,
                    "evidence": "một câu không có trong văn bản nguồn", "explanation": "Bịa"},
                   {"paragraph_id": "p13", "claim": "x", "verdict": "sai", "evidence": "y"},
               ]}
        issues, verified, dropped = content_ai.filter_grounded(raw, {12: self.paragraph})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "fail")
        self.assertEqual(dropped, 2)

    def test_review_filter_drops_spelling_and_unquoted(self):
        raw = [{"paragraph_id": "p12", "quote": "gửi báo cáo về Sở", "category": "dien_dat",
                "problem": "Lỗi chính tả ở từ báo cáo", "confidence": 0.9},
               {"paragraph_id": "p12", "quote": "không có trong đoạn", "category": "logic", "problem": "Mâu thuẫn"},
               {"paragraph_id": "p12", "quote": "trước ngày 20/9/2026", "category": "thoi_han",
                "problem": "Thời hạn mâu thuẫn với đoạn 15", "confidence": 0.95}]
        kept, dropped = content_ai.filter_review(raw, {12: self.paragraph})
        self.assertEqual([item["category"] for item in kept], ["thoi_han"])
        self.assertEqual(kept[0]["severity"], "warn")
        self.assertEqual(dropped, 2)

    def test_source_passages_keep_relevant_blocks(self):
        text = "\n".join([f"Mục {i}: nội dung chung không liên quan số {i}." * 8 for i in range(40)]
                         + ["Thời hạn gửi báo cáo chuyển đổi số trước ngày 15/9/2026."])
        passages = content_ai.source_passages(text, ["gửi báo cáo chuyển đổi số trước ngày 20/9/2026"], limit=2000)
        self.assertIn("15/9/2026", passages)
        self.assertLessEqual(len(passages), 2200)


class RunCodeTest(unittest.TestCase):
    def test_docx_end_to_end_without_network(self):
        document = docx.Document()
        for text in ["UBND TỈNH LÀO CAI", "SỞ KHOA HỌC VÀ CÔNG NGHỆ", "Số: 120/SKHCN-VP",
                     "Lào Cai, ngày 30 tháng 9 năm 2026", "KẾ HOẠCH", "Triển khai chuyển đổi số",
                     "Căn cứ Nghị định số 30/2020/NĐ-CP ngày 05/3/2020 của Chính phủ về công tác văn thư;",
                     "Căn cứ Quyết định số 75/QĐ-SKHCN ngày 17/3/2026 của Giám đốc Sở.",
                     "Sở Khoa học và Công nghệ ban hành Kế hoạch số 120/SKHCN-VP; họp vào thứ Hai, ngày 06/10/2026.",
                     "Nơi nhận:", "- Như trên;"]:
            document.add_paragraph(text)
        handle, path = tempfile.mkstemp(suffix=".docx")
        os.close(handle)
        try:
            document.save(path)
            with mock.patch.object(references.store, "search_documents_by_reference", return_value=[]), \
                    mock.patch.object(references.store, "find_doc_by_soky", return_value=[]):
                report, _, refs, meta = content_review.run_code(path, "kh.docx")
        finally:
            os.remove(path)
        self.assertEqual(meta["number"], "120/SKHCN-VP")
        self.assertEqual(meta["date"], "2026-09-30")
        self.assertEqual({ref.identifier for ref in refs}, {"30/2020/NĐ-CP", "75/QĐ-SKHCN"})  # không tự dẫn chính mình
        self.assertIn("weekday", [item["rule"] for item in report["logic"]])
        self.assertEqual(report["summary"]["references"], 2)


if __name__ == "__main__":
    unittest.main()
