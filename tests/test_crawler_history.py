import json
import os
import tempfile
import unittest

from src.crawler.history import CrawlHistory, source_key


class CrawlHistoryTests(unittest.TestCase):
    def test_reference_key_is_stable_and_direction_is_separate(self):
        key = source_key(" 123/QĐ - SKHCN ", "01/01/2026", "A")
        self.assertEqual(key, source_key("123/QĐ-SKHCN", "01/01/2026", "B"))
        self.assertNotEqual(key, source_key("123/QĐ-SKHCN", "02/02/2026", "A"))
        with tempfile.TemporaryDirectory() as directory:
            with CrawlHistory(os.path.join(directory, "history.sqlite3")) as history:
                history.mark_completed([("di", key, "123/QĐ-SKHCN")])
                self.assertTrue(history.contains("di", key))
                self.assertFalse(history.contains("den", key))

    def test_missing_reference_uses_document_identity(self):
        first = source_key("", "01/01/2026", "  Báo cáo   tháng 1 ")
        second = source_key("", "01/01/2026", "Báo cáo tháng 1")
        self.assertEqual(first, second)

    def test_scan_completion_is_tracked_per_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            with CrawlHistory(os.path.join(directory, "history.sqlite3")) as history:
                self.assertFalse(history.has_reached_end("di"))
                history.mark_reached_end("di")
                self.assertTrue(history.has_reached_end("di"))
                self.assertFalse(history.has_reached_end("den"))
                history.mark_incomplete("di")
                self.assertFalse(history.has_reached_end("di"))

    def test_legacy_json_is_imported_once_until_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "history.sqlite3")
            legacy = os.path.join(directory, "downloaded_records_di.json")
            with open(legacy, "w", encoding="utf-8") as stream:
                json.dump(["01/QĐ-TEST", "02/QĐ-TEST"], stream)
            with CrawlHistory(database) as history:
                self.assertEqual(2, history.import_legacy_json(legacy, "di"))
                self.assertEqual(0, history.import_legacy_json(legacy, "di"))
                self.assertTrue(history.contains("di", source_key("01/QĐ-TEST")))


if __name__ == "__main__":
    unittest.main()
