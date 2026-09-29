"""Mở trình duyệt để người dùng thao tác trên QLVB và ghi lại các API mà giao diện gọi.

Dùng để khảo sát API (danh sách Văn bản Đi/Đến, chi tiết, tệp đính kèm, tải tệp) phục vụ crawler API.
Token, cookie, mật khẩu, captcha bị thay bằng [REDACTED]; phản hồi của SSO không được lưu.
Kết quả: data/captures/<thời điểm>/network.jsonl (mỗi dòng một sự kiện) và downloads.jsonl.
Đóng cửa sổ trình duyệt để kết thúc.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

START_URL = os.getenv("QLVB_URL", "https://egov1.laocai.gov.vn").rstrip("/") + "/"
MAX_BODY_CHARS = 200_000
RELEVANT_HOST_RE = re.compile(r"(laocai\.gov\.vn|vnpthub\.vn)$")
SSO_HOST_RE = re.compile(r"(sso|identity|login|auth)", re.IGNORECASE)
SENSITIVE_KEY_RE = re.compile(
    r"(authorization|cookie|password|passwd|captcha|token|secret|api[-_]?key|signature|credential|otp)",
    re.IGNORECASE,
)
# One-time OAuth codes in query strings (e.g. ProcessAuthCode?code=...) are credentials too.
SENSITIVE_QUERY_KEY_RE = re.compile(r"^(code|state|session_state)$", re.IGNORECASE)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def clean_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        query = urlencode([(key, "[REDACTED]" if SENSITIVE_KEY_RE.search(key) or SENSITIVE_QUERY_KEY_RE.match(key) else value)
                           for key, value in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except ValueError:
        return clean_text(url)


def clean_text(value: str) -> str:
    value = BEARER_RE.sub("Bearer [REDACTED]", value)
    value = JWT_RE.sub("[REDACTED_JWT]", value)
    return re.sub(r"([?&](?:token|access_token|refresh_token|code)=)[^&#\s\"']+", r"\1[REDACTED]", value)


def clean(value, key: str = ""):
    if key and SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, str):
        return clean_url(value) if value.startswith(("http://", "https://")) else clean_text(value)
    return value


def clean_body(text: str | None, content_type: str):
    if not text:
        return None
    if "json" in content_type:
        try:
            return clean(json.loads(text))
        except ValueError:
            pass
    if "x-www-form-urlencoded" in content_type or "multipart" in content_type:
        return "[FORM_BODY_REDACTED]"
    text = clean_text(text)
    return text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS] + "…[TRUNCATED]"


def relevant(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return bool(RELEVANT_HOST_RE.search(host))


async def main() -> int:
    out_dir = ROOT / "data" / "captures" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    network_path, downloads_path = out_dir / "network.jsonl", out_dir / "downloads.jsonl"
    (ROOT / "data" / "captures" / "latest.txt").write_text(str(out_dir), encoding="utf-8")
    counter = {"n": 0}

    def write(path: Path, record: dict) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await browser.new_context(accept_downloads=True, ignore_https_errors=True,
                                            viewport={"width": 1400, "height": 860}, locale="vi-VN")
        page = await context.new_page()

        async def on_response(response):
            request = response.request
            if request.resource_type not in {"xhr", "fetch", "document"} or not relevant(request.url):
                return
            host = urlsplit(request.url).hostname or ""
            counter["n"] += 1
            record = {
                "seq": counter["n"],
                "time": datetime.now().isoformat(timespec="seconds"),
                "page": clean_url(page.url),
                "method": request.method,
                "type": request.resource_type,
                "url": clean_url(request.url),
                "status": response.status,
                "request_headers": clean({k: v for k, v in request.headers.items()
                                          if k.lower() in {"authorization", "content-type", "accept", "x-requested-with"}}),
                "request_body": clean_body(request.post_data, request.headers.get("content-type", "")),
                "response_type": response.headers.get("content-type", ""),
            }
            if SSO_HOST_RE.search(host):
                record["response_body"] = "[SSO_RESPONSE_NOT_STORED]"
            elif "json" in record["response_type"]:
                try:
                    record["response_body"] = clean_body(await response.text(), record["response_type"])
                except Exception as exc:  # body may be unavailable for redirects/aborted requests
                    record["response_body"] = f"[BODY_UNAVAILABLE: {type(exc).__name__}]"
            write(network_path, record)
            if request.resource_type in {"xhr", "fetch"}:
                print(f"[{counter['n']}] {request.method} {response.status} {clean_url(request.url)[:160]}", flush=True)

        async def on_download(download):
            record = {"time": datetime.now().isoformat(timespec="seconds"), "page": clean_url(page.url),
                      "url": clean_url(download.url), "suggested_filename": download.suggested_filename}
            write(downloads_path, record)
            print(f"[download] {download.suggested_filename} <- {record['url'][:160]}", flush=True)
            await download.cancel()  # chỉ cần biết cách tải, không lưu tệp

        context.on("response", lambda response: asyncio.create_task(on_response(response)))
        page.on("download", lambda download: asyncio.create_task(on_download(download)))
        context.on("page", lambda new_page: new_page.on("download",
                                                         lambda d: asyncio.create_task(on_download(d))))
        await page.goto(START_URL)
        print(f"Đang ghi vào {out_dir}. Đóng cửa sổ trình duyệt để kết thúc.", flush=True)
        closed = asyncio.Event()
        browser.on("disconnected", lambda _: closed.set())
        context.on("close", lambda _: closed.set())
        page.on("close", lambda _: closed.set() if not context.pages else None)
        await closed.wait()
        try:
            await browser.close()
        except Exception:
            pass
    print(f"Kết thúc. {counter['n']} phản hồi đã ghi: {network_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
