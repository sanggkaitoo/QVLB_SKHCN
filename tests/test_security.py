import asyncio
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from fastapi.security import HTTPBasicCredentials

from src.core import config
from src.utils import auth
from src.utils.uploads import save_upload


def request(ip="10.0.0.1", headers=None):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers=headers or {})


class AdminAuthTests(unittest.TestCase):
    def setUp(self):
        auth._failures.clear()

    def test_weak_password_locks_admin(self):
        for weak in ("", "matkhau123", "short"):
            with patch.object(config, "ADMIN_PASS", weak), self.assertRaises(HTTPException) as error:
                auth.verify_admin(request(), HTTPBasicCredentials(username="admin", password=weak))
            self.assertEqual(503, error.exception.status_code)

    def test_lockout_after_repeated_failures(self):
        with patch.object(config, "ADMIN_PASS", "a-strong-password"), patch.object(config, "ADMIN_MAX_FAILED_LOGINS", 3):
            for _ in range(3):
                with self.assertRaises(HTTPException) as error:
                    auth.verify_admin(request(), HTTPBasicCredentials(username="admin", password="wrong"))
                self.assertEqual(401, error.exception.status_code)
            with self.assertRaises(HTTPException) as error:
                auth.verify_admin(request(), HTTPBasicCredentials(username="admin", password="a-strong-password"))
            self.assertEqual(429, error.exception.status_code)
            # Another client is unaffected.
            self.assertEqual("admin", auth.verify_admin(request("10.0.0.2"), HTTPBasicCredentials(username="admin", password="a-strong-password")))

    def test_proxy_header_only_when_trusted(self):
        forwarded = request("127.0.0.1", {"cf-connecting-ip": "203.0.113.9"})
        with patch.object(config, "TRUST_PROXY_HEADERS", False):
            self.assertEqual("127.0.0.1", auth.client_ip(forwarded))
        with patch.object(config, "TRUST_PROXY_HEADERS", True):
            self.assertEqual("203.0.113.9", auth.client_ip(forwarded))


class UploadTests(unittest.TestCase):
    def test_size_and_extension_limits(self):
        async def scenario():
            with self.assertRaises(HTTPException) as error:
                await save_upload(UploadFile(io.BytesIO(b"x"), filename="virus.exe"), {".docx"}, 1)
            self.assertEqual(415, error.exception.status_code)
            with self.assertRaises(HTTPException) as error:
                await save_upload(UploadFile(io.BytesIO(b"x" * (2 * 1024 * 1024)), filename="big.docx"), {".docx"}, 1)
            self.assertEqual(413, error.exception.status_code)
            path = await save_upload(UploadFile(io.BytesIO(b"data"), filename="ok.DOCX"), {".docx"}, 1)
            try:
                self.assertTrue(path.endswith(".docx"))
                with open(path, "rb") as stream:
                    self.assertEqual(b"data", stream.read())
            finally:
                os.unlink(path)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
