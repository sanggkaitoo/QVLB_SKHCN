import os
import tempfile
import unittest
from unittest.mock import patch

from src.core import config
from src.services import ingest, source_store

DIGEST = "ab" + "c" * 62


class SourceStoreTests(unittest.TestCase):
    def _touch(self, path: str, data: bytes = b"x" * 10) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(data)
        return path

    def test_prepare_does_not_archive_when_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            store_dir = os.path.join(directory, "store")
            source = self._touch(os.path.join(directory, "dl", "01_a.docx"))
            with patch.object(config, "STORE_DIR", store_dir), \
                 patch.object(source_store, "keep_enabled", return_value=False):
                [item] = ingest._prepare([ingest.SourceFile(source, 1, text="nội dung", method="docx")])
            self.assertEqual("", item.archived_path)
            self.assertFalse(os.path.exists(store_dir))
            with patch.object(config, "STORE_DIR", store_dir), \
                 patch.object(source_store, "keep_enabled", return_value=True):
                [item] = ingest._prepare([ingest.SourceFile(source, 1, text="nội dung", method="docx")])
            self.assertTrue(item.archived_path.startswith(store_dir) and os.path.isfile(item.archived_path))

    def test_prepare_reuses_database_text_without_original(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "STORE_DIR", directory), \
             patch.object(source_store, "keep_enabled", return_value=False):
            reused, lost = ingest._prepare([
                ingest.SourceFile("", 1, text="nội dung", method="pdf_text", name="01_a.pdf", digest=DIGEST),
                ingest.SourceFile("", 2, name="02_b.pdf", digest="d" * 64),
            ])
        self.assertEqual(("01_a.pdf", DIGEST, "nội dung", "", None),
                         (reused.name, reused.digest, reused.text, reused.archived_path, reused.error))
        self.assertIn("Không còn tệp gốc", lost.error)

    def test_purge_only_removes_archived_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            store_dir = os.path.join(directory, "store")
            original = self._touch(os.path.join(store_dir, "ab", DIGEST, "01_a.pdf"), b"x" * 100)
            self._touch(original + ".meta.json")
            self._touch(os.path.join(store_dir, "failed_ingest", "bad.xls"))
            kept = [
                self._touch(os.path.join(store_dir, "crawler_state.sqlite3")),
                self._touch(os.path.join(store_dir, "crawler_diagnostics", "login-page.png")),
                self._touch(os.path.join(store_dir, "ab", "not-a-digest", "keep.txt")),
                self._touch(os.path.join(store_dir, "zz", DIGEST, "keep.pdf")),
            ]
            outside = self._touch(os.path.join(directory, "postgres", "PG_VERSION"))
            os.symlink(os.path.dirname(outside), os.path.join(store_dir, "cd"))  # liên kết ra ngoài: không đụng tới
            with patch.object(config, "STORE_DIR", store_dir), \
                 patch.object(source_store, "keep_enabled", return_value=False):
                self.assertEqual({"files": 2, "bytes": 120}, {k: v for k, v in source_store.usage(True).items()
                                                              if k != "path"})
                self.assertIsNone(source_store.start_purge())
                for _ in range(200):
                    if not source_store.purge_status()["running"]:
                        break
                    threading_wait()
                status = source_store.purge_status()
                self.assertEqual((3, 120, None), (status["deleted_files"], status["freed_bytes"], status["error"]))
                self.assertEqual(0, source_store.usage()["files"])
            self.assertFalse(os.path.exists(os.path.join(store_dir, "ab", DIGEST)))
            self.assertFalse(os.path.exists(os.path.join(store_dir, "failed_ingest")))
            for path in kept + [outside]:
                self.assertTrue(os.path.exists(path), path)

    def test_purge_refused_while_keeping(self):
        with patch.object(source_store, "keep_enabled", return_value=True):
            self.assertIn("tắt", source_store.start_purge())


def threading_wait():
    import time
    time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
