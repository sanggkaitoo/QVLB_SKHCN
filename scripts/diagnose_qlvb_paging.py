"""Chẩn đoán phân trang danh sách văn bản QLVB (dòng trùng mã: trùng thật hay phân trang không ổn định).

Mở trình duyệt để người dùng đăng nhập; token chỉ giữ trong RAM. Sau đó với mỗi hướng:
  1. Quét toàn bộ với cỡ trang 50 và 20, so sánh tập mã văn bản.
  2. Liệt kê các mã xuất hiện nhiều lần: vị trí (trang, dòng) và dữ liệu có giống nhau không.
  3. Thử tham số sắp xếp order_col/order_type xem máy chủ có hỗ trợ không.
Kết quả: data/captures/paging-<thời điểm>.json (chỉ mã văn bản, số ký hiệu, vị trí; không có token).
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright  # noqa: E402

from src.core import config  # noqa: E402
from src.crawler.qlvb_api import DIRECTIONS, QlvbApiClient  # noqa: E402
from src.crawler.sso import _token_from_payload  # noqa: E402


async def get_token() -> str:
    holder: dict = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False, args=["--no-sandbox"])
        context = await browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 800})

        async def capture(response):
            if "ProcessAuthCode" in response.url and not holder.get("token"):
                try:
                    holder["token"], _ = _token_from_payload(await response.json())
                except Exception:
                    pass
        context.on("response", lambda response: asyncio.create_task(capture(response)))
        page = await context.new_page()
        await page.goto(config.QLVB_URL + "/")
        print("Hãy đăng nhập trong cửa sổ trình duyệt vừa mở (tối đa 10 phút)...", flush=True)
        for _ in range(1200):
            if holder.get("token"):
                break
            await asyncio.sleep(0.5)
        await browser.close()
    if not holder.get("token"):
        raise SystemExit("Không nhận được token (chưa đăng nhập hoặc hết thời gian).")
    print("Đã đăng nhập, bắt đầu chẩn đoán.", flush=True)
    return holder["token"]


async def sweep(client: QlvbApiClient, direction: str, length: int, extra: dict | None = None):
    rows, skip, page, total = [], 0, 1, None
    spec = DIRECTIONS[direction]
    while True:
        params = {"page": page, "length": length, "term": "", "archiveSearch": "false", **spec.list_params,
                  "isLoading": "true", "defer": "true", "skip": skip, **(extra or {})}
        value = await client._get_value(spec.list_path, params) or {}
        data = [row for row in value.get("data") or [] if isinstance(row, dict)]
        total = int(value.get("total") or 0)
        if not data:
            break
        for position, row in enumerate(data):
            rows.append({"id": str(row.get("macongvan")), "page": page, "pos": position, "skip": skip + position,
                         "sohieu": row.get("sohieu"), "date": row.get("ngaytao") or row.get("ngayden"),
                         "so": row.get("sodi") or row.get("soden"), "sig": json.dumps(row, sort_keys=True, ensure_ascii=False)})
        skip += len(data)
        page += 1
        if skip >= total:
            break
        await asyncio.sleep(0.2)
    return total, rows


def analyse(rows: list[dict]) -> dict:
    by_id = defaultdict(list)
    for row in rows:
        by_id[row["id"]].append(row)
    duplicates = {key: value for key, value in by_id.items() if len(value) > 1}
    identical = sum(1 for value in duplicates.values() if len({row["sig"] for row in value}) == 1)
    return {
        "rows": len(rows), "unique": len(by_id), "duplicate_ids": len(duplicates),
        "duplicates_identical_rows": identical,
        "examples": [[{k: row[k] for k in ("page", "pos", "skip", "sohieu", "date", "so")} for row in value]
                     for value in list(duplicates.values())[:8]],
    }


async def main() -> int:
    token = await get_token()
    client = QlvbApiClient(token)
    report = {"time": datetime.now().isoformat(timespec="seconds")}
    try:
        for direction in ("di", "den"):
            label = DIRECTIONS[direction].label
            print(f"== {label}: quét cỡ trang 50...", flush=True)
            total50, rows50 = await sweep(client, direction, 50)
            print(f"== {label}: quét cỡ trang 20...", flush=True)
            total20, rows20 = await sweep(client, direction, 20)
            ids50, ids20 = {r["id"] for r in rows50}, {r["id"] for r in rows20}
            sort_tests = {}
            date_col = DIRECTIONS[direction].date_field
            spec = DIRECTIONS[direction]
            for col in ("macongvan", date_col, "stt", "sohieu"):
                try:
                    value = await client._get_value(spec.list_path, {
                        "page": 1, "length": 10, "term": "", "archiveSearch": "false", **spec.list_params,
                        "isLoading": "true", "defer": "true", "skip": 0, "order_col": col, "order_type": "desc"}) or {}
                    first = value.get("data") or []
                    sort_tests[col] = {"total": value.get("total"), "first_values": [r.get(col) for r in first[:6]]}
                except Exception as exc:
                    sort_tests[col] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            report[direction] = {
                "total_reported": [total50, total20],
                "size50": analyse(rows50), "size20": analyse(rows20),
                "union_unique": len(ids50 | ids20), "only_in_50": len(ids50 - ids20), "only_in_20": len(ids20 - ids50),
                "default_first_ids": [r["id"] for r in rows50[:5]],
                "sort_tests": sort_tests,
            }
            r = report[direction]
            print(f"   tổng báo {total50}/{total20}; khác nhau: cỡ 50 = {r['size50']['unique']}, cỡ 20 = {r['size20']['unique']}, "
                  f"hợp = {r['union_unique']}; mã trùng cỡ 50 = {r['size50']['duplicate_ids']} "
                  f"(dòng giống hệt: {r['size50']['duplicates_identical_rows']})", flush=True)
    finally:
        await client.close()
    out = ROOT / "data" / "captures" / f"paging-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Xong. Báo cáo: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
