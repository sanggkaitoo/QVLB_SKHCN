import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src.core import config
from src.services import ingest
from src.utils import extract as extractor


class IngestBatchTests(unittest.TestCase):
    def _write_item(self, directory: str, filename: str, document_ref: str) -> str:
        file_path = os.path.join(directory, filename)
        with open(file_path, "wb") as stream:
            stream.write(b"test")
        with open(file_path + ".meta.json", "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "so_ky_hieu": document_ref,
                    "ngay_ban_hanh": "01/01/2026",
                    "trich_yeu": f"Văn bản {document_ref}",
                    "huong": "den",
                    "history_key": f"history:{document_ref}",
                },
                stream,
                ensure_ascii=False,
            )
        return file_path

    def test_failed_file_is_quarantined_and_next_document_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            download_dir = os.path.join(directory, "downloads")
            store_dir = os.path.join(directory, "store")
            os.makedirs(download_dir)
            bad_path = self._write_item(download_dir, "bad.xls", "01/TEST")
            good_path = self._write_item(download_dir, "good.xls", "02/TEST")
            calls = []

            def fake_ingest(file_path, **kwargs):
                calls.append(os.path.basename(file_path))
                if file_path == bad_path:
                    raise ingest.ExtractionError("broken xls")
                return 42

            with (
                patch.object(config, "STORE_DIR", store_dir),
                patch.object(ingest.store, "ensure_collection"),
                patch.object(ingest, "ingest_file", side_effect=fake_ingest),
            ):
                result = ingest.ingest_download_dir(download_dir)

            self.assertEqual(["bad.xls", "good.xls"], calls)
            self.assertEqual(1, result["processed_files"])
            self.assertEqual(1, len(result["failed_files"]))
            self.assertEqual(
                ["history:02/TEST"],
                [item["history_key"] for item in result["completed_documents"]],
            )
            self.assertFalse(os.path.exists(bad_path))
            self.assertFalse(os.path.exists(good_path))
            failed_dir = os.path.join(store_dir, "failed_ingest")
            self.assertTrue(os.path.exists(os.path.join(failed_dir, "bad.xls")))
            self.assertTrue(os.path.exists(os.path.join(failed_dir, "bad.xls.meta.json")))


    def test_legacy_xls_uses_xlrd_engine(self):
        with patch.object(extractor.pd, "ExcelFile") as excel_file:
            excel_file.return_value.__enter__.return_value.sheet_names = []
            text, method = extractor.extract_excel("legacy.xls")

        excel_file.assert_called_once_with("legacy.xls", engine="xlrd")
        self.assertEqual("", text)
        self.assertEqual("xls", method)

    def test_csv_is_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sample.csv")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("ten,so_luong\nA,12\n")

            text, method = extractor.extract(path)

        self.assertEqual("csv", method)
        self.assertIn("so_luong", text)
        self.assertIn("12", text)
if __name__ == "__main__":
    unittest.main()
