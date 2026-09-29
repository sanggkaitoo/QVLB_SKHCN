import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.core import config
from src.crawler import qlvb_api, sso


def list_row(i, files=None, domat="thường"):
    return {"macongvan": f"ID{i:04d}", "sohieu": f"{i}/SKHCN-VP", "trichyeu": f"Văn bản {i}",
            "ngaytao": "29/09/2026", "domat": domat, "loaivanban": " Công văn", "files": files or [f"f{i}.pdf"]}


def client_with(handler):
    client = qlvb_api.QlvbApiClient("Bearer abc.def")
    client._client = httpx.AsyncClient(base_url=config.QLVB_API_BASE_URL, transport=httpx.MockTransport(handler),
                                       headers=client._client.headers)
    return client


class ClientTests(unittest.TestCase):
    def test_list_page_uses_observed_parameters_and_bearer(self):
        seen = {}

        def handler(request):
            seen.update(url=str(request.url), auth=request.headers["authorization"])
            return httpx.Response(200, json={"code": "OK", "value": {"total": 3710, "data": [list_row(1)]}})

        async def scenario():
            client = client_with(handler)
            try:
                return await client.list_page("den", 2, 50, 50)
            finally:
                await client.close()
        total, rows = asyncio.run(scenario())
        self.assertEqual(3710, total)
        self.assertEqual("ID0001", rows[0]["macongvan"])
        self.assertIn("IworkVanThuHandler.ashx", seen["url"])
        self.assertIn("type=xulyden", seen["url"])
        self.assertIn("trangthai=2", seen["url"])
        self.assertIn("skip=50", seen["url"])
        self.assertEqual("Bearer abc.def", seen["auth"])

    def test_unauthorized_is_session_expired(self):
        async def scenario():
            client = client_with(lambda request: httpx.Response(401))
            try:
                await client.detail("di", "X")
            finally:
                await client.close()
        with self.assertRaises(qlvb_api.SessionExpired):
            asyncio.run(scenario())

    def test_gateway_not_found_application_means_not_logged_in(self):
        async def scenario():
            client = client_with(lambda request: httpx.Response(200, text="Not found application: ioffice"))
            try:
                await client.list_page("di", 1, 10, 0)
            finally:
                await client.close()
        with self.assertRaises(qlvb_api.SessionExpired):
            asyncio.run(scenario())

    def test_repeated_login_rejections_stop_the_job(self):
        session = qlvb_api._Session()
        session.renewals = qlvb_api._Session.MAX_RENEWALS
        with self.assertRaises(qlvb_api.QlvbApiError):
            asyncio.run(session.renew())

    def test_download_only_from_storage_host_and_sends_token(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, content=b"%PDF-1.4 data")

        async def scenario(url):
            client = client_with(handler)
            try:
                with tempfile.TemporaryDirectory() as directory:
                    return await client.download(url, os.path.join(directory, "a.pdf"))
            finally:
                await client.close()
        size = asyncio.run(scenario("https://egov-storage1.laocai.gov.vn/get?key=lci%2Fioffice%2Fa.pdf"))
        self.assertEqual(13, size)
        self.assertIn("key=lci%2Fioffice%2Fa.pdf", captured["url"])
        self.assertIn("token=abc.def", captured["url"])
        with self.assertRaises(qlvb_api.QlvbApiError):
            asyncio.run(scenario("https://evil.example.com/get?key=x"))

    def test_mapping_and_classification(self):
        record = qlvb_api._list_record("di", {**list_row(7), "domat": "Mật"})
        self.assertEqual(("skipped", "2026-09-29"), (record["status"], record["ngay"]))
        self.assertEqual("pending", qlvb_api._list_record("den", {**list_row(8), "ngaybh": "02/05/2026"})["status"])
        self.assertEqual("2026-05-02", qlvb_api._list_record("den", {**list_row(8), "ngaybh": "02/05/2026"})["ngay"])
        self.assertNotEqual(qlvb_api._fingerprint(list_row(1)), qlvb_api._fingerprint(list_row(1, ["f1.pdf", "f2.pdf"])))
        self.assertFalse(qlvb_api.is_classified("Thường"))
        self.assertTrue(qlvb_api.is_classified("Tối mật"))


class PagedServer:
    """Imitates QLVB: pages by page number, optionally returns one extra row per page, may cap page size."""

    def __init__(self, rows, extra_row=False, cap=None):
        self.rows, self.extra_row, self.cap, self.calls = rows, extra_row, cap, []

    async def list_page(self, direction, page, length, skip, newest_first=False):
        self.calls.append((page, length, newest_first))
        size = min(length, self.cap or length)
        start = (page - 1) * size
        source = sorted(self.rows, key=lambda r: r["macongvan"], reverse=True) if newest_first else self.rows
        return len(self.rows), source[start:start + size + (1 if self.extra_row else 0)]


def run_sweep(server, page_size):
    session = qlvb_api._Session()
    session.client = server
    upserted = []
    with patch.object(qlvb_api, "_sweep", return_value=1), \
         patch.object(qlvb_api, "_finish_sweep") as finish, \
         patch.object(qlvb_api, "_mark_not_in_source") as mark, \
         patch.object(qlvb_api, "upsert_list_rows", side_effect=lambda d, r: (upserted.extend(r) or (len(r), 0))), \
         patch.object(config, "CRAWLER_API_DELAY_MS", 0):
        result = asyncio.run(qlvb_api._full_sweep(session, "den", page_size=page_size))
    return result, upserted, finish, mark


class SweepTests(unittest.TestCase):
    def test_extra_row_per_page_does_not_end_the_sweep_early(self):
        rows = [list_row(i) for i in range(1000)]
        for size in (50, 20):
            result, upserted, finish, mark = run_sweep(PagedServer(rows, extra_row=True), size)
            self.assertEqual(1000, len({row["macongvan"] for row in upserted}), size)
            self.assertEqual(1000, result["seen"])
            self.assertEqual(len(upserted), 1000)  # repeated boundary rows are not upserted twice
            self.assertTrue(finish.call_args.kwargs["completed"])
            mark.assert_called_once()

    def test_server_page_cap_is_detected_on_first_page(self):
        server = PagedServer([list_row(i) for i in range(25)], cap=10)
        result, upserted, _, _ = run_sweep(server, 50)
        self.assertEqual(25, len({row["macongvan"] for row in upserted}))
        self.assertEqual([(1, 50, False), (2, 10, False), (3, 10, False)], server.calls)

    def test_genuine_duplicate_rows_are_counted_once(self):
        rows = [list_row(i) for i in range(30)] + [list_row(3), list_row(7)]
        result, _, _, _ = run_sweep(PagedServer(rows), 10)
        self.assertEqual((32, 30), (result["total"], result["seen"]))

    def test_page_size_is_bounded(self):
        self.assertEqual(500, qlvb_api._page_size(5000))
        self.assertEqual(10, qlvb_api._page_size(3))
        self.assertEqual(config.CRAWLER_API_PAGE_SIZE, qlvb_api._page_size(None))

    def _quick(self, server, known, previous_total):
        session = qlvb_api._Session()
        session.client = server
        with patch.object(qlvb_api, "known_ids", side_effect=lambda d, ids: {i for i in ids if i in known}), \
             patch.object(qlvb_api, "last_complete_total", return_value=previous_total), \
             patch.object(qlvb_api, "upsert_list_rows", return_value=(0, 0)) as upsert, \
             patch.object(qlvb_api, "_sweep", return_value=1), patch.object(qlvb_api, "_finish_sweep"), \
             patch.object(qlvb_api, "_full_sweep", new=AsyncMock(return_value={"new": 0})) as full, \
             patch.object(config, "CRAWLER_API_DELAY_MS", 0):
            result = asyncio.run(qlvb_api._quick_check(session, "di", page_size=10))
        return result, upsert, full

    def test_quick_check_reads_newest_pages_only(self):
        rows = [list_row(i) for i in range(100)]
        known = {f"ID{i:04d}" for i in range(88)}          # 12 newest documents are new
        server = PagedServer(rows)
        result, upsert, full = self._quick(server, known, previous_total=88)
        full.assert_not_called()
        self.assertEqual(12, result["new"])
        self.assertTrue(all(call[2] for call in server.calls))   # sorted newest first
        self.assertEqual(3, len(server.calls))                    # 2 pages with new documents + 1 fully known page

    def test_quick_check_falls_back_when_counts_disagree(self):
        rows = [list_row(i) for i in range(100)]
        known = {f"ID{i:04d}" for i in range(100)}
        _, _, full = self._quick(PagedServer(rows), known, previous_total=97)   # something changed deeper in the list
        full.assert_awaited_once()
        _, _, full = self._quick(PagedServer(rows), known, previous_total=None)  # never swept fully
        full.assert_awaited_once()


class FakeLocator:
    def __init__(self, src="a"):
        self.src = src

    async def get_attribute(self, name):
        return self.src

    async def screenshot(self):
        return b"png"


class LoginWaitTests(unittest.TestCase):
    def setUp(self):
        sso.crawler_state.update(login_data=None, login_action=None, stop_requested=False, captcha_version=0,
                                 captcha_expires_at=time.time() + 60)

    def test_cancel_raises(self):
        sso.crawler_state["login_action"] = "cancel"
        form = {"captcha_image": FakeLocator(), "frame": None}
        with self.assertRaises(sso.LoginCancelled):
            asyncio.run(sso._wait_for_login_data(None, form, "https://x", 1))

    def test_refresh_request_and_expiry_publish_new_captcha(self):
        form = {"captcha_image": FakeLocator(), "frame": None}
        sso.crawler_state["login_action"] = "refresh"

        async def scenario():
            async def submit_later():
                await asyncio.sleep(0.7)
                sso.crawler_state["login_data"] = {"username": "u", "password": "p", "captcha": "c"}
            asyncio.create_task(submit_later())
            return await sso._wait_for_login_data(None, form, "https://x", 1)

        with patch.object(sso, "_refresh_captcha", new=AsyncMock(return_value=True)) as refresh:
            data, _ = asyncio.run(scenario())
        refresh.assert_awaited_once()
        self.assertEqual("u", data["username"])
        self.assertEqual(1, sso.crawler_state["captcha_version"])
        self.assertIn("Đã đổi mã", sso.crawler_state["message"])

        sso.crawler_state.update(captcha_expires_at=time.time() - 1, login_action=None)

        async def expiry():
            async def submit_later():
                await asyncio.sleep(0.7)
                sso.crawler_state["login_data"] = {"username": "u"}
            asyncio.create_task(submit_later())
            return await sso._wait_for_login_data(None, form, "https://x", 1)

        with patch.object(sso, "_refresh_captcha", new=AsyncMock(return_value=True)):
            asyncio.run(expiry())
        self.assertIn("hết hạn", sso.crawler_state["message"])
        self.assertGreater(sso.crawler_state["captcha_expires_at"], time.time())

    def test_token_parsing(self):
        token, user = sso._token_from_payload({"code": "OK", "value": {"token": {"accessToken": "t"}, "user": {"fullName": "A"}}})
        self.assertEqual(("t", "A"), (token, user))


if __name__ == "__main__":
    unittest.main()
