import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.core import config
from src.services import ingest
from src.utils import extract as extractor


class IngestBatchTests(unittest.TestCase):
    def _write_item(self, directory: str, filename: str, document_ref: str, index: int = 1) -> str:
        file_path = os.path.join(directory, filename)
        with open(file_path, "wb") as stream:
            stream.write(b"test")
        with open(file_path + ".meta.json", "w", encoding="utf-8") as stream:
            json.dump({"so_ky_hieu": document_ref, "ngay_ban_hanh": "01/01/2026", "trich_yeu": f"Văn bản {document_ref}",
                       "huong": "den", "history_key": f"history:{document_ref}", "file_index": index},
                      stream, ensure_ascii=False)
        return file_path

    def _run(self, download_dir, store_dir, fake, keep=True):
        with patch.object(config, "STORE_DIR", store_dir), \
             patch.object(ingest.source_store, "keep_enabled", return_value=keep), \
             patch.object(ingest.store, "ensure_collection"), \
             patch.object(ingest, "ingest_document", side_effect=fake) as call:
            return ingest.ingest_download_dir(download_dir), call

    def test_files_of_one_document_are_ingested_together(self):
        with tempfile.TemporaryDirectory() as directory:
            download_dir, store_dir = os.path.join(directory, "downloads"), os.path.join(directory, "store")
            os.makedirs(download_dir)
            main = self._write_item(download_dir, "01_TEST_01_main.pdf", "01/TEST", 1)
            annex = self._write_item(download_dir, "01_TEST_02_annex.docx", "01/TEST", 2)
            other = self._write_item(download_dir, "02_TEST_01_main.pdf", "02/TEST", 1)
            result, call = self._run(download_dir, store_dir,
                                     lambda files, **kwargs: {"document_id": 1, "skipped": False, "failed_files": []})
            self.assertEqual(2, call.call_count)
            grouped = [[os.path.basename(f.path) for f in c.args[0]] for c in call.call_args_list]
            self.assertIn(["01_TEST_01_main.pdf", "01_TEST_02_annex.docx"], grouped)
            self.assertEqual(3, result["processed_files"])
            self.assertEqual(2, len(result["completed_documents"]))
            for path in (main, annex, other):
                self.assertFalse(os.path.exists(path))

    def test_failed_attachment_is_quarantined_and_document_not_checkpointed(self):
        with tempfile.TemporaryDirectory() as directory:
            download_dir, store_dir = os.path.join(directory, "downloads"), os.path.join(directory, "store")
            os.makedirs(download_dir)
            self._write_item(download_dir, "01_TEST_01_main.pdf", "01/TEST", 1)
            bad = self._write_item(download_dir, "01_TEST_02_bad.xls", "01/TEST", 2)
            good = self._write_item(download_dir, "02_TEST_01_main.pdf", "02/TEST", 1)

            def fake(files, **kwargs):
                failed = [(f.path, "broken xls") for f in files if f.path == bad]
                return {"document_id": 1, "skipped": False, "failed_files": failed}
            result, _ = self._run(download_dir, store_dir, fake)
            self.assertEqual(["history:02/TEST"], [item["history_key"] for item in result["completed_documents"]])
            self.assertEqual(1, len(result["failed_files"]))
            self.assertTrue(os.path.exists(os.path.join(store_dir, "failed_ingest", "01_TEST_02_bad.xls")))
            self.assertFalse(os.path.exists(good))

    def test_failed_attachment_is_deleted_when_source_files_are_not_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            download_dir, store_dir = os.path.join(directory, "downloads"), os.path.join(directory, "store")
            os.makedirs(download_dir)
            self._write_item(download_dir, "01_TEST_01_main.pdf", "01/TEST", 1)
            bad = self._write_item(download_dir, "01_TEST_02_bad.xls", "01/TEST", 2)
            self._write_item(download_dir, "01_TEST_03_note.zip", "01/TEST", 3)

            def fake(files, **kwargs):
                return {"document_id": 1, "skipped": False, "failed_files": [(bad, "broken xls")]}
            result, _ = self._run(download_dir, store_dir, fake, keep=False)
            self.assertEqual(1, len(result["failed_files"]))
            self.assertNotIn("quarantined_file", result["failed_files"][0])
            self.assertEqual([], os.listdir(download_dir))     # tải về đã dọn, kể cả tệp không hỗ trợ
            self.assertFalse(os.path.exists(store_dir))         # không ghi gì vào kho tệp gốc

    def test_unreadable_document_quarantines_all_files(self):
        with tempfile.TemporaryDirectory() as directory:
            download_dir, store_dir = os.path.join(directory, "downloads"), os.path.join(directory, "store")
            os.makedirs(download_dir)
            self._write_item(download_dir, "01_TEST_01_main.pdf", "01/TEST", 1)

            def fake(files, **kwargs):
                raise ingest.ExtractionError("no text")
            result, _ = self._run(download_dir, store_dir, fake)
            self.assertEqual([], result["completed_documents"])
            self.assertEqual(1, len(result["failed_files"]))

    def test_duplicate_content_files_are_clustered(self):
        body = " ".join(f"nội dung văn bản số {i}" for i in range(60))
        prepared = [
            ingest._PreparedFile(ingest.SourceFile("a.pdf", 1), "a.pdf", "d1", "a", text=body, method="pdf_text"),
            ingest._PreparedFile(ingest.SourceFile("b.pdf", 2), "b.pdf", "d2", "b", text=body + " ký số", method="pdf_mixed_ocr"),
            ingest._PreparedFile(ingest.SourceFile("c.docx", 3), "c.docx", "d3", "c", text=body, method="docx"),
            ingest._PreparedFile(ingest.SourceFile("d.pdf", 4), "d.pdf", "d4", "d",
                                 text=" ".join(f"phụ lục khác {i}" for i in range(60)), method="pdf_text"),
        ]
        ingest.assign_roles(prepared)
        self.assertEqual(["chinh", "ban_sao", "ban_sao", "dinh_kem"], [item.role for item in prepared])
        self.assertEqual(0, prepared[1].duplicate_of)

    def test_ocr_copy_is_replaced_by_cleaner_text_extraction(self):
        body = " ".join(f"nội dung văn bản số {i}" for i in range(60))
        prepared = [
            ingest._PreparedFile(ingest.SourceFile("scan.pdf", 1), "scan.pdf", "d1", "a", text=body, method="pdf_mixed_ocr"),
            ingest._PreparedFile(ingest.SourceFile("draft.docx", 2), "draft.docx", "d2", "b", text=body, method="docx"),
        ]
        ingest.assign_roles(prepared)
        self.assertEqual(["ban_sao", "chinh"], [item.role for item in prepared])

    def test_ocr_noise_still_detected_but_excerpt_is_not_a_copy(self):
        words = [f"từ{i}" for i in range(400)]
        noisy = list(words)
        for i in range(0, 400, 25):
            noisy[i] = "nhiễu"  # OCR errors on ~4% of words
        excerpt = " ".join(words[:120])
        prepared = [
            ingest._PreparedFile(ingest.SourceFile("a.pdf", 1), "a.pdf", "d1", "a", text=" ".join(words), method="pdf_text"),
            ingest._PreparedFile(ingest.SourceFile("b.pdf", 2), "b.pdf", "d2", "b", text=" ".join(noisy), method="pdf_mixed_ocr"),
            ingest._PreparedFile(ingest.SourceFile("c.pdf", 3), "c.pdf", "d3", "c", text=excerpt, method="pdf_text"),
        ]
        ingest.assign_roles(prepared)
        self.assertEqual(["chinh", "ban_sao", "dinh_kem"], [item.role for item in prepared])

    def test_source_key_uses_crawler_identity(self):
        self.assertEqual("den|ref:1/TEST|date:01/01/2026",
                         ingest.source_key_for({"history_key": "ref:1/TEST|date:01/01/2026"}, "den", "abc"))
        self.assertEqual("sha256:abc", ingest.source_key_for({}, "di", "abc"))
        self.assertEqual(3, ingest.file_index_from_name("2072_SKHCN-QLCN_03_Vanban.docx"))

    def test_legacy_xls_uses_xlrd_engine(self):
        sheet = SimpleNamespace(name="Biểu 1", nrows=3,
                                row_values=lambda i: [["STT", "Nội dung"], [1.0, "Tập huấn"], ["", ""]][i])
        book = SimpleNamespace(nsheets=1, sheet_by_index=lambda i: sheet, unload_sheet=lambda i: None,
                               release_resources=lambda: None)
        with patch("xlrd.open_workbook", return_value=book) as open_workbook:
            text, method = extractor.extract_excel("legacy.xls")
        open_workbook.assert_called_once_with("legacy.xls", on_demand=True)
        self.assertIn("Dòng 2: 1 | Tập huấn", text)
        self.assertEqual("xls", method)

    def test_csv_is_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sample.csv")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("ten,so_luong\nA,12\n")
            text, method = extractor.extract(path)
        self.assertEqual("csv", method)
        self.assertIn("so_luong", text)

    def test_extraction_errors_are_explicit(self):
        with self.assertRaises(extractor.ExtractionError):
            extractor.extract("file.xyz")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "broken.docx")
            with open(path, "wb") as stream:
                stream.write(b"not a zip")
            with self.assertRaisesRegex(extractor.ExtractionError, "broken.docx"):
                extractor.extract(path)


if __name__ == "__main__":
    unittest.main()
