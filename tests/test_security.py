import asyncio
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from src.core import config, llm, security
from src.services import model_catalog
from src.utils.uploads import save_upload


def request(ip="10.0.0.1", headers=None, method="POST", cookies=None):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers=headers or {}, method=method, cookies=cookies or {})


def user(role, user_id=1):
    return security.User(id=user_id, username=f"u{user_id}", full_name=None, email=None, role=role, is_active=True,
                         daily_quota=0, permissions=set(security.ROLE_PERMISSIONS[role]))


class PasswordTests(unittest.TestCase):
    def test_hash_roundtrip_and_salt(self):
        first, second = security.hash_password("Mat-khau-123"), security.hash_password("Mat-khau-123")
        self.assertNotEqual(first, second)
        self.assertTrue(security.verify_password("Mat-khau-123", first))
        self.assertFalse(security.verify_password("mat-khau-123", first))
        self.assertFalse(security.verify_password("x", "not-a-hash"))

    def test_password_rules(self):
        self.assertIsNotNone(security.password_problem("short1"))
        self.assertIsNotNone(security.password_problem("chiconchucai"))
        self.assertIsNotNone(security.password_problem("nguyenvan123", "nguyenvan"))
        self.assertIsNone(security.password_problem("Lao-Cai-2026", "admin"))


class RoleTests(unittest.TestCase):
    def test_hierarchy(self):
        root, admin = user("super_admin", 1), user("admin", 2)
        self.assertTrue(security.can_manage(root, "super_admin", 9))
        self.assertTrue(security.can_manage(admin, "staff", 9))
        self.assertFalse(security.can_manage(admin, "admin", 9))
        self.assertFalse(security.can_manage(admin, "super_admin", 9))
        self.assertFalse(security.can_manage(admin, "viewer", admin.id))  # không tự quản lý qua quyền cấp trên
        self.assertFalse(security.can_manage(user("operator", 3), "viewer", 9))

    def test_permissions(self):
        self.assertTrue(user("viewer").can("tools.search"))
        self.assertFalse(user("viewer").can("tools.check"))
        self.assertFalse(user("staff").can("crawler.run"))
        self.assertTrue(user("operator").can("crawler.run"))
        self.assertFalse(user("admin").can("settings.manage"))
        self.assertTrue(user("super_admin").can("settings.manage"))


class RequestGuardTests(unittest.TestCase):
    def setUp(self):
        security._failures.clear()

    def test_csrf_header_required_for_writes(self):
        self.assertTrue(security.csrf_ok(request(method="GET")))
        self.assertFalse(security.csrf_ok(request()))
        self.assertTrue(security.csrf_ok(request(headers={"x-requested-with": "DocNexus"})))

    def test_lockout(self):
        with patch.object(config, "ADMIN_MAX_FAILED_LOGINS", 3):
            for _ in range(3):
                self.assertFalse(security.login_blocked("10.0.0.1|admin"))
                security.record_login_failure("10.0.0.1|admin")
            self.assertTrue(security.login_blocked("10.0.0.1|admin"))
            self.assertFalse(security.login_blocked("10.0.0.2|admin"))
            security.clear_login_failures("10.0.0.1|admin")
            self.assertFalse(security.login_blocked("10.0.0.1|admin"))

    def test_proxy_header_only_when_trusted(self):
        forwarded = request("127.0.0.1", {"cf-connecting-ip": "203.0.113.9"})
        with patch.object(config, "TRUST_PROXY_HEADERS", False):
            self.assertEqual("127.0.0.1", security.client_ip(forwarded))
        with patch.object(config, "TRUST_PROXY_HEADERS", True):
            self.assertEqual("203.0.113.9", security.client_ip(forwarded))


class ModelRoutingTests(unittest.TestCase):
    CATALOG = {
        "qwen/qwen-2.5-72b-instruct": {"id": "qwen/qwen-2.5-72b-instruct", "name": "Qwen", "inputs": ["text"],
                                       "prompt": 0.36, "completion": 0.4},
        "google/gemini-2.5-flash": {"id": "google/gemini-2.5-flash", "name": "Gemini Flash",
                                    "inputs": ["audio", "file", "image", "text"], "prompt": 0.3, "completion": 2.5},
        "anthropic/claude-sonnet-5.5": {"id": "anthropic/claude-sonnet-5.5", "name": "Claude Sonnet 5.5",
                                        "inputs": ["file", "image", "text"], "prompt": 2.0, "completion": 10.0},
    }

    def test_split_spec(self):
        self.assertEqual(("openrouter", "google/gemini-2.5-flash"), llm.split_spec("google/gemini-2.5-flash"))
        self.assertEqual(("openrouter", "deepseek/r1:free"), llm.split_spec("deepseek/r1:free"))
        self.assertEqual(("anthropic", "claude-sonnet-5-5"), llm.split_spec("anthropic:claude-sonnet-5-5"))

    def test_task_input_check(self):
        with patch.object(model_catalog, "_by_id", return_value=self.CATALOG):
            self.assertFalse(model_catalog.supports("qwen/qwen-2.5-72b-instruct", "transcribe")[0])
            self.assertTrue(model_catalog.supports("google/gemini-2.5-flash", "transcribe")[0])
            self.assertFalse(model_catalog.supports("qwen/qwen-2.5-72b-instruct", "ocr")[0])
            self.assertTrue(model_catalog.supports("anthropic:claude-sonnet-5-5", "ocr")[0])
            self.assertFalse(model_catalog.supports("anthropic:claude-sonnet-5-5", "transcribe")[0])
            self.assertTrue(model_catalog.supports("openai:gpt-4o-transcribe", "transcribe")[0])
            self.assertFalse(model_catalog.supports("openai:gpt-4o-transcribe", "answer")[0])
            self.assertFalse(model_catalog.supports("local:Unlimited-OCR", "answer")[0])
            self.assertEqual(2.0, model_catalog.describe("anthropic:claude-sonnet-5-5")["prompt"])

    def test_role_resolution_prefers_admin_setting(self):
        with patch("src.core.app_settings.get", return_value={"answer": "openai:gpt-5-mini"}):
            self.assertEqual("openai:gpt-5-mini", llm.resolve("@answer"))
            self.assertEqual(config.LLM_CHEAP, llm.resolve("@plan"))
            self.assertEqual(config.LLM_CHEAP, llm.resolve("@cheap"))


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
