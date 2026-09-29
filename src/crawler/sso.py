"""Đăng nhập SSO QLVB bằng Playwright (captcha do người dùng nhập) và giữ token API trong bộ nhớ.

Dùng chung cho crawler giao diện (spider) và crawler API:
- Ảnh captcha được gửi lên trang admin; người dùng nhập tài khoản, mật khẩu, captcha.
- Người dùng có thể yêu cầu đổi mã hoặc huỷ đăng nhập; mã tự đổi khi quá CRAWLER_CAPTCHA_TTL_SECONDS
  (SSO không công bố thời hạn captcha) và trang đăng nhập được mở lại sau CRAWLER_LOGIN_PAGE_MAX_AGE_SECONDS.
- Token lấy từ phản hồi ProcessAuthCode chỉ nằm trong RAM, không ghi log/đĩa.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.core import config

logger = logging.getLogger(__name__)

crawler_state: dict[str, Any] = {
    "status": "idle",
    "job": None,               # "spider" | "api_sync" | "api_download"
    "captcha_b64": None,
    "captcha_version": 0,
    "captcha_expires_at": None,
    "login_data": None,
    "login_action": None,      # "refresh" | "cancel"
    "stop_requested": False,
    "message": "",
    "progress": {},
}

_session: dict[str, Any] = {"token": None, "obtained_at": None, "user": None}

ACTIVE_STATES = {"starting", "waiting_login", "logging_in", "crawling", "stopping"}

USERNAME_SELECTORS = (
    "input#usernameUserInput",
    "input[name='usernameUserInput']",
    "input[autocomplete='username']",
    "input[name='username']:not([type='hidden'])",
    "input[type='email']",
)
PASSWORD_SELECTORS = ("input#password", "input[name='password']", "input[type='password']")
CAPTCHA_INPUT_SELECTORS = (
    "input#captcha",
    "input[name='captcha']",
    "input[placeholder*='xác thực' i]",
    "input[placeholder*='captcha' i]",
)
CAPTCHA_IMAGE_SELECTORS = ("img#captchaImage", "img[id*='captcha' i]", "img[src*='captcha' i]")
SUBMIT_SELECTORS = (
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Đăng nhập')",
    "button:has-text('Login')",
)


class LoginCancelled(RuntimeError):
    """Người dùng huỷ đăng nhập từ trang admin."""


class CrawlStopped(RuntimeError):
    """Người dùng yêu cầu dừng tác vụ crawl."""


def check_stop() -> None:
    if crawler_state.get("stop_requested"):
        raise CrawlStopped("Đã dừng theo yêu cầu.")


def access_token() -> str | None:
    return _session["token"]


def forget_token() -> None:
    _session.update(token=None, obtained_at=None, user=None)


def session_info() -> dict:
    return {"logged_in": bool(_session["token"]), "user": _session["user"],
            "obtained_at": _session["obtained_at"]}


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


async def _first_visible(frame, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = frame.locator(selector).first
        try:
            if await locator.count() and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def find_login_form(page) -> dict[str, Any] | None:
    for frame in page.frames:
        username = await _first_visible(frame, USERNAME_SELECTORS)
        password = await _first_visible(frame, PASSWORD_SELECTORS)
        captcha_input = await _first_visible(frame, CAPTCHA_INPUT_SELECTORS)
        captcha_image = await _first_visible(frame, CAPTCHA_IMAGE_SELECTORS)
        submit = await _first_visible(frame, SUBMIT_SELECTORS)
        if username and password and captcha_input and captcha_image and submit:
            return {"frame": frame, "username": username, "password": password,
                    "captcha_input": captcha_input, "captcha_image": captcha_image, "submit": submit}
    return None


async def _wait_for_login_form(page, timeout_ms: int) -> dict[str, Any] | None:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        form = await find_login_form(page)
        if form:
            return form
        await asyncio.sleep(0.5)
    return None


async def goto_with_retry(page, url: str, label: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, config.CRAWLER_NAVIGATION_RETRIES + 1):
        crawler_state["message"] = f"Đang mở {label} (lần {attempt}/{config.CRAWLER_NAVIGATION_RETRIES})..."
        try:
            response = await page.goto(url, wait_until="domcontentloaded",
                                       timeout=config.CRAWLER_PAGE_TIMEOUT_SECONDS * 1000)
            if response and response.status >= 500:
                raise RuntimeError(f"HTTP {response.status}")
            return
        except Exception as exc:
            last_error = exc
            if attempt < config.CRAWLER_NAVIGATION_RETRIES:
                await asyncio.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"Không mở được {label}: {last_error}")


async def _capture_login_diagnostic(page) -> None:
    try:
        directory = Path(config.STORE_DIR) / "crawler_diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(directory / "login-page.png"), full_page=True)
    except Exception:
        pass


async def open_login_form(page, qlvb_url: str) -> dict[str, Any]:
    last_location = qlvb_url
    for attempt in range(1, config.CRAWLER_LOGIN_RETRIES + 1):
        check_login_cancelled()
        try:
            await goto_with_retry(page, qlvb_url, "cổng QLVB/SSO")
            last_location = page.url
            form = await _wait_for_login_form(page, config.CRAWLER_LOGIN_FORM_TIMEOUT_SECONDS * 1000)
            if form:
                return form
        except LoginCancelled:
            raise
        except Exception:
            last_location = page.url
        await _capture_login_diagnostic(page)
        crawler_state["message"] = f"Chưa nhận được form SSO, đang thử lại ({attempt}/{config.CRAWLER_LOGIN_RETRIES})..."
        await asyncio.sleep(min(2 ** attempt, 10))
    raise RuntimeError("Không tìm thấy form đăng nhập SSO sau nhiều lần điều hướng. "
                       f"Trang cuối: {_safe_url(last_location)}. Ảnh chẩn đoán đã được lưu.")


def check_login_cancelled() -> None:
    if crawler_state.get("login_action") == "cancel" or crawler_state.get("stop_requested"):
        crawler_state["login_action"] = None
        raise LoginCancelled("Đã huỷ đăng nhập.")


async def _publish_captcha(form, attempt: int, note: str = "") -> None:
    image = await form["captcha_image"].screenshot()
    crawler_state.update({
        "captcha_b64": base64.b64encode(image).decode("ascii"),
        "captcha_version": int(crawler_state.get("captcha_version") or 0) + 1,
        "captcha_expires_at": time.time() + config.CRAWLER_CAPTCHA_TTL_SECONDS,
        "status": "waiting_login",
        "message": (note + " " if note else "") +
                   f"Nhập tài khoản và captcha SSO (lần {attempt}/{config.CRAWLER_LOGIN_RETRIES}).",
    })


async def _refresh_captcha(form) -> bool:
    """Ask the SSO page for a new captcha (its own refresh control); True when the image changed."""
    frame = form["frame"]
    before = await form["captcha_image"].get_attribute("src")
    try:
        refresh = frame.locator("#resendCaptcha").first
        if await refresh.count():
            await refresh.click(timeout=5_000)
        else:
            await frame.evaluate("() => generateCaptchaLogin(getParameterByName('sessionDataKey'))")
    except Exception as exc:
        logger.info("Không bấm được nút đổi captcha: %s", exc)
        return False
    for _ in range(40):
        await asyncio.sleep(0.25)
        try:
            if await form["captcha_image"].get_attribute("src") != before:
                return True
        except Exception:
            return False
    return False


async def _wait_for_login_data(page, form, qlvb_url: str, attempt: int) -> tuple[dict[str, str], dict]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + config.CRAWLER_LOGIN_INPUT_TIMEOUT_SECONDS
    page_opened = loop.time()
    while loop.time() < deadline:
        check_login_cancelled()
        data = crawler_state.get("login_data")
        if data:
            crawler_state["login_data"] = None
            return data, form
        action = crawler_state.get("login_action")
        expired = time.time() >= float(crawler_state.get("captcha_expires_at") or 0)
        if action == "refresh" or expired:
            crawler_state["login_action"] = None
            note = "Mã cũ đã hết hạn, đã tạo mã mới." if expired and action != "refresh" else "Đã đổi mã xác thực."
            if loop.time() - page_opened >= config.CRAWLER_LOGIN_PAGE_MAX_AGE_SECONDS or not await _refresh_captcha(form):
                # A fresh SSO page also renews the login session (sessionDataKey).
                form = await open_login_form(page, qlvb_url)
                page_opened = loop.time()
            await _publish_captcha(form, attempt, note)
        await asyncio.sleep(0.5)
    raise TimeoutError("Hết thời gian chờ nhập tài khoản và captcha.")


async def _wait_for_application(page, qlvb_url: str) -> bool:
    target_host = urlparse(qlvb_url).hostname
    deadline = asyncio.get_running_loop().time() + config.CRAWLER_LOGIN_RESULT_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        parsed = urlparse(page.url)
        path = parsed.path.lower()
        if parsed.hostname == target_host and (path.startswith("/home/") or path.startswith("/document/")):
            return True
        await asyncio.sleep(0.5)
    return False


def _token_from_payload(payload: dict) -> tuple[str | None, str | None]:
    value = payload.get("value") or {}
    token = value.get("token")
    if isinstance(token, dict):
        token = token.get("accessToken") or token.get("access_token") or token.get("token")
    user = value.get("user") or {}
    return (str(token).strip() if token else None), (user.get("fullName") or user.get("userName"))


async def login(page, qlvb_url: str) -> None:
    """Đăng nhập SSO; lưu token API trong RAM. Ném LoginCancelled nếu người dùng huỷ."""
    crawler_state["login_action"] = None
    token_holder: dict[str, Any] = {}

    async def capture(response):
        if "ProcessAuthCode" not in response.url or token_holder.get("token"):
            return
        try:
            payload = await response.json()
            if payload.get("code") == "OK":
                token_holder["token"], token_holder["user"] = _token_from_payload(payload)
        except Exception:
            return

    listener = lambda response: asyncio.create_task(capture(response))  # noqa: E731
    page.context.on("response", listener)
    try:
        form = await find_login_form(page) or await open_login_form(page, qlvb_url)
        for attempt in range(1, config.CRAWLER_LOGIN_RETRIES + 1):
            if not form:
                form = await open_login_form(page, qlvb_url)
            await _publish_captcha(form, attempt)
            data, form = await _wait_for_login_data(page, form, qlvb_url, attempt)
            crawler_state.update({"status": "logging_in", "message": "Đang xác thực với cổng SSO...",
                                  "captcha_b64": None, "captcha_expires_at": None})
            await form["username"].fill(data.get("username", ""))
            await form["password"].fill(data.get("password", ""))
            await form["captcha_input"].fill(data.get("captcha", ""))
            data.clear()
            await form["submit"].click(timeout=15_000)

            if await _wait_for_application(page, qlvb_url):
                for _ in range(40):  # ProcessAuthCode usually completes right after the redirect
                    if token_holder.get("token"):
                        break
                    await asyncio.sleep(0.25)
                if token_holder.get("token"):
                    _session.update(token=token_holder["token"], obtained_at=time.time(), user=token_holder.get("user"))
                crawler_state.update(login_data=None, captcha_b64=None, captcha_expires_at=None)
                return

            crawler_state["message"] = "Đăng nhập chưa thành công; đang làm mới captcha..."
            form = await find_login_form(page)
            if form and not await _refresh_captcha(form):
                form = None
        raise RuntimeError("Đăng nhập SSO thất bại sau số lần thử cho phép.")
    finally:
        page.context.remove_listener("response", listener)
        crawler_state.update(login_data=None, login_action=None)


async def obtain_token(qlvb_url: str | None = None) -> str:
    """Mở trình duyệt headless chỉ để đăng nhập, trả token API (captcha do người dùng nhập)."""
    from playwright.async_api import async_playwright

    qlvb_url = (qlvb_url or config.QLVB_URL).rstrip("/")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = await browser.new_context(viewport={"width": 1280, "height": 720},
                                            ignore_https_errors=config.CRAWLER_IGNORE_HTTPS_ERRORS)
        context.set_default_timeout(config.CRAWLER_ACTION_TIMEOUT_SECONDS * 1000)
        try:
            page = await context.new_page()
            await login(page, qlvb_url)
        finally:
            await context.close()
            await browser.close()
    token = access_token()
    if not token:
        raise RuntimeError("Đăng nhập thành công nhưng không nhận được token API từ QLVB.")
    return token
