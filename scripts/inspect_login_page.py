from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()
url = os.getenv("QLVB_URL", "https://egov1.laocai.gov.vn")
output = Path("reports/generated/qlvb-login-current.png")
output.parent.mkdir(parents=True, exist_ok=True)


def visible_elements(frame, selector: str, expression: str):
    try:
        return frame.locator(selector).evaluate_all(expression)
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox"],
    )
    context = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1440, "height": 1000},
    )
    page = context.new_page()
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(f"{message.type}: {message.text}")
        if message.type in {"error", "warning"}
        else None,
    )
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(8_000)

    frames = []
    for frame in page.frames:
        frames.append(
            {
                "url": frame.url,
                "name": frame.name,
                "inputs": visible_elements(
                    frame,
                    "input",
                    """els => els.map(e => ({
                        id: e.id, name: e.name, type: e.type,
                        placeholder: e.placeholder, autocomplete: e.autocomplete,
                        visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)
                    }))""",
                ),
                "buttons": visible_elements(
                    frame,
                    "button,input[type=submit]",
                    """els => els.map(e => ({
                        tag: e.tagName, id: e.id, name: e.name, type: e.type,
                        text: (e.innerText || e.value || '').trim(),
                        visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)
                    }))""",
                ),
                "images": visible_elements(
                    frame,
                    "img",
                    """els => els.map(e => ({
                        id: e.id,
                        src: e.src.startsWith('data:') ? 'data:image' : new URL(e.src).pathname,
                        alt: e.alt,
                        visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)
                    })).filter(e => e.visible)""",
                ),
            }
        )

    page.screenshot(path=str(output), full_page=True)
    print(
        json.dumps(
            {
                "final_url": page.url,
                "title": page.title(),
                "frames": frames,
                "console_errors": console_errors[-20:],
                "screenshot": str(output),
            },
            ensure_ascii=False,
        )
    )
    browser.close()
