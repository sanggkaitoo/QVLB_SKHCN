"""Tra cứu văn bản pháp luật trên CSDL quốc gia về pháp luật (vbpl.vn) — tuân thủ robots.txt của vbpl.vn.

robots.txt của vbpl.vn cấm máy truy cập /api/, cho phép các trang công khai. Vì vậy:
  - Danh mục: đọc sitemap công khai (https://vbpl.vn/sitemap.xml) → bảng legal_pages. Lấy toàn bộ văn bản
    Trung ương và văn bản địa phương của Lào Cai / Yên Bái (đã hợp nhất). Số ký hiệu trích từ đầu địa chỉ
    ("nghi-dinh-so-30-2020-nd-cp-…" → 30/2020).
  - Tra cứu: mở trang chi tiết công khai và đọc khối dữ liệu cấu trúc schema.org/Legislation (số ký hiệu,
    loại, ngày ban hành, cơ quan, tình trạng hiệu lực) → bộ nhớ đệm legal_documents (7 ngày).
Mọi yêu cầu đi tuần tự, cách nhau tối thiểu LEGAL_FETCH_INTERVAL giây, có định danh User-Agent.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
import unicodedata

import httpx
import psycopg2.extras

from src.core import app_settings, config, store

logger = logging.getLogger(__name__)

SITEMAP_INDEX = "https://vbpl.vn/sitemap.xml"
USER_AGENT = "DocNexus/1.0 (So Khoa hoc va Cong nghe Lao Cai; kiem tra can cu van ban)"
LOCAL_KEEP = ("lao-cai", "yen-bai")
CACHE_DAYS = 7
STATUS_KEY = "legal.index"
_FORCE = {
    "InForce": ("pass", "Còn hiệu lực"),
    "PartiallyInForce": ("info", "Hết hiệu lực một phần"),   # vẫn dẫn được; chỉ lưu ý điều khoản được dẫn
    "NotInForce": ("fail", "Hết hiệu lực"),
    "NotYetInForce": ("warn", "Chưa có hiệu lực"),
}
_NUM_YEAR = re.compile(r"(?:^|-)so-(\d+[a-z]?)-(\d{4})-")
_fetch_lock = threading.Lock()
_last_fetch = 0.0
_build_lock = threading.Lock()


def _client() -> httpx.Client:
    return httpx.Client(timeout=config.LEGAL_FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT},
                        follow_redirects=True)


def _polite_get(client: httpx.Client, url: str) -> httpx.Response:
    """Một yêu cầu tại một thời điểm, cách nhau tối thiểu LEGAL_FETCH_INTERVAL giây."""
    global _last_fetch
    with _fetch_lock:
        wait = config.LEGAL_FETCH_INTERVAL - (time.monotonic() - _last_fetch)
        if wait > 0:
            time.sleep(wait)
        try:
            return client.get(url)
        finally:
            _last_fetch = time.monotonic()


def ascii_fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "").replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def normalize_identifier(identifier: str) -> str:
    # vbpl.vn đôi khi ghi số có dấu nháy thừa phía trước: "'12/2025/QĐ-UBND".
    return re.sub(r"^[^0-9A-Z]+", "", re.sub(r"\s+", "", ascii_fold(identifier)).upper())


# ------------------------------------------------------------------ danh mục từ sitemap

def status() -> dict:
    info = app_settings.get(STATUS_KEY) or {}
    if not isinstance(info, dict):
        info = {}
    return {**info, "building": _build_lock.locked()}


def needs_refresh() -> bool:
    built = status().get("built_at")
    if not built:
        return True
    try:
        return dt.datetime.fromisoformat(built) < dt.datetime.now() - dt.timedelta(days=config.LEGAL_INDEX_MAX_AGE_DAYS)
    except ValueError:
        return True


def build_index() -> dict:
    """Đọc sitemap vbpl.vn và cập nhật bảng legal_pages. Chạy nền; một lần tại một thời điểm."""
    if not _build_lock.acquire(blocking=False):
        return {"status": "busy"}
    started = time.monotonic()
    counts = {"central": 0, "local": 0, "with_number": 0}
    try:
        app_settings.put(STATUS_KEY, {**status(), "building": True, "error": None})
        with _client() as client:
            index = _polite_get(client, SITEMAP_INDEX).text
            # Chú thích trong sitemap phân nhóm "Trung ương" / "Địa phương".
            scope, entries = None, []
            for line in index.splitlines():
                if "Trung ương" in line:
                    scope = "central"
                elif "Địa phương" in line:
                    scope = "local"
                match = re.search(r"<loc>([^<]+)</loc>", line)
                if match and scope:
                    entries.append((scope, match.group(1)))
            for scope, sitemap_url in entries:
                text = _polite_get(client, sitemap_url).text
                rows = []
                for url in re.findall(r"<loc>([^<]+)</loc>", text):
                    slug = url.rsplit("/chi-tiet/", 1)[-1]
                    if scope == "local" and not any(key in slug for key in LOCAL_KEEP):
                        continue
                    match = _NUM_YEAR.search(slug.split("--")[0])
                    num_year = f"{match.group(1)}/{match.group(2)}" if match else None
                    rows.append((url, scope, num_year, slug.split("--")[0][:500]))
                    counts[scope] += 1
                    counts["with_number"] += bool(num_year)
                if rows:
                    with store.pg() as connection, connection.cursor() as cursor:
                        psycopg2.extras.execute_values(
                            cursor,
                            """INSERT INTO legal_pages (url, scope, num_year, slug) VALUES %s
                               ON CONFLICT (url) DO UPDATE SET num_year = EXCLUDED.num_year, slug = EXCLUDED.slug,
                                                               seen_at = now()""", rows, page_size=1000)
        result = {"built_at": dt.datetime.now().isoformat(timespec="seconds"), "counts": counts,
                  "seconds": round(time.monotonic() - started), "error": None}
        app_settings.put(STATUS_KEY, result)
        logger.info("Đã cập nhật danh mục vbpl.vn: %s", counts)
        return result
    except Exception as exc:
        logger.warning("Không cập nhật được danh mục vbpl.vn: %s", exc)
        app_settings.put(STATUS_KEY, {**status(), "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        return {"status": "error", "error": str(exc)}
    finally:
        _build_lock.release()


def build_in_background() -> bool:
    if _build_lock.locked():
        return False
    threading.Thread(target=build_index, name="legal-index", daemon=True).start()
    return True


# ------------------------------------------------------------------ tra cứu

def _cached(normalized: str) -> list[dict]:
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute("SELECT * FROM legal_documents WHERE normalized_identifier = %s "
                       "AND fetched_at > now() - make_interval(days => %s) ORDER BY fetched_at DESC",
                       (normalized, CACHE_DAYS))
        return [dict(row) for row in cursor.fetchall()]


def _title_score(title: str, name: str | None) -> float:
    left = {w for w in re.findall(r"[a-z0-9]+", ascii_fold(title).lower()) if len(w) > 2}
    right = {w for w in re.findall(r"[a-z0-9]+", ascii_fold(name or "").lower()) if len(w) > 2}
    return len(left & right) / len(left) if left and right else 0.0


def _best(records: list[dict], title: str, issued: dt.date | None) -> dict:
    """Nhiều văn bản trùng số (VD Lào Cai trước/sau hợp nhất năm 2025): chọn theo ngày rồi theo trích yếu."""
    def score(record):
        day = record.get("issued")
        day = day.isoformat() if hasattr(day, "isoformat") else day
        return (2 if issued and day == issued.isoformat() else 0) + _title_score(title, record.get("name"))
    return max(records, key=score)


def _candidates(identifier: str, title_words: list[str], prefer: str = "") -> list[str]:
    parts = identifier.split("/")
    number = parts[0].lower().replace(".", "-")   # "66.18/2026/NQ-CP" → địa chỉ "…-so-66-18-2026-nq-cp-…"
    code_slug = re.sub(r"[^a-z0-9]+", "-", ascii_fold("/".join(parts[2:])).lower()).strip("-")
    with store.pg() as connection, connection.cursor() as cursor:
        if len(parts) >= 3:
            cursor.execute("SELECT url, slug FROM legal_pages WHERE num_year = %s", (f"{parts[0]}/{parts[1]}".lower(),))
            rows = cursor.fetchall()
            exact = [(url, slug) for url, slug in rows
                     if re.search(rf"(?:^|-)so-{re.escape(number)}-{parts[1]}-{re.escape(code_slug)}(?:-|$)", slug)]
            # Lào Cai và Yên Bái (đã hợp nhất) có thể trùng số: ưu tiên tỉnh được nhắc trong dự thảo.
            if not exact:  # địa chỉ không có chữ "so": "nghi-dinh-73-2026-nd-cp-…"
                cursor.execute("SELECT url, slug FROM legal_pages WHERE slug ~ %s LIMIT 5",
                               (rf"^[a-z-]*?(-so)?-{re.escape(number)}-{parts[1]}-{re.escape(code_slug)}(-|$)",))
                exact = cursor.fetchall()
            exact.sort(key=lambda item: 0 if prefer and prefer in item[1] else 1)
            if exact:
                return [url for url, _ in exact[:4]]
        if len(title_words) >= 3:  # địa chỉ không có số: tìm theo tên văn bản
            pattern = "%" + "%".join(title_words[:6]) + "%"
            cursor.execute("SELECT url FROM legal_pages WHERE num_year IS NULL AND slug LIKE %s LIMIT 3", (pattern,))
            return [row[0] for row in cursor.fetchall()]
    return []


def _legislation(client: httpx.Client, url: str) -> dict | None:
    response = _polite_get(client, url)
    if response.status_code != 200:
        return None
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', response.text, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if data.get("@type") == "Legislation":
            # vbpl.vn đôi khi ghi số có dấu nháy thừa: "'12/2025/QĐ-UBND".
            identifier = re.sub(r"^[^0-9A-Za-zĐđ]+", "", (data.get("legislationIdentifier") or "").strip())
            return {**data, "legislationIdentifier": identifier, "name": re.sub(r"'(?=\d)", "", data.get("name") or "")}
    return None


def _save(url: str, data: dict) -> dict:
    issued = (data.get("legislationDate") or "")[:10] or None
    record = {"url": url, "identifier": data.get("legislationIdentifier"),
              "normalized_identifier": normalize_identifier(data.get("legislationIdentifier") or ""),
              "doc_type": data.get("legislationType"), "issued": issued,
              "legal_force": data.get("legislationLegalForce"),
              "issuer": (data.get("legislationPassedBy") or {}).get("name"), "name": data.get("name")}
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO legal_documents (url, identifier, normalized_identifier, doc_type, issued, legal_force,
                                            issuer, name, fetched_at)
               VALUES (%(url)s, %(identifier)s, %(normalized_identifier)s, %(doc_type)s, %(issued)s, %(legal_force)s,
                       %(issuer)s, %(name)s, now())
               ON CONFLICT (url) DO UPDATE SET identifier = EXCLUDED.identifier,
                   normalized_identifier = EXCLUDED.normalized_identifier, doc_type = EXCLUDED.doc_type,
                   issued = EXCLUDED.issued, legal_force = EXCLUDED.legal_force, issuer = EXCLUDED.issuer,
                   name = EXCLUDED.name, fetched_at = now()""", record)
    return record


def lookup(identifier: str, title: str = "", client: httpx.Client | None = None, issuer: str = "",
           issued: dt.date | None = None) -> dict:
    """Tình trạng một văn bản pháp luật. Trả {"status": found|not_found|unavailable, ...}."""
    normalized = normalize_identifier(identifier)
    title_words = [w for w in re.sub(r"[^a-z0-9 ]", " ", ascii_fold(title).lower()).split() if len(w) > 2]
    try:
        cached = {record["url"]: record for record in _cached(normalized)}
        folded = ascii_fold(issuer).lower()
        prefer = "yen-bai" if "yen bai" in folded else ("lao-cai" if "lao cai" in folded else "")
        urls = _candidates(identifier, title_words, prefer)
    except Exception as exc:  # migration chưa chạy
        return {"status": "unavailable", "message": f"Chưa có danh mục văn bản pháp luật ({type(exc).__name__})."}
    records = [cached[url] for url in urls if url in cached] or list(cached.values())
    pending = [url for url in urls if url not in cached]
    exact_date = bool(issued) and any(str(record.get("issued"))[:10] == issued.isoformat() for record in records)
    if records and (not pending or exact_date):
        return _result(_best(records, title, issued), len(records))
    if not urls:
        return {"status": "not_found"}
    own = client is None
    client = client or _client()
    try:
        for url in pending:
            data = _legislation(client, url)
            if data and normalize_identifier(data.get("legislationIdentifier") or "") == normalized:
                records.append(_save(url, data))
        if not records:
            return {"status": "not_found"}
        return _result(_best(records, title, issued), len(records))
    except httpx.HTTPError as exc:
        if records:
            return _result(_best(records, title, issued), len(records))
        return {"status": "unavailable", "message": f"Không kết nối được vbpl.vn ({type(exc).__name__})."}
    finally:
        if own:
            client.close()


def lookup_named(doc_type: str, title: str, issued: dt.date, client: httpx.Client | None = None) -> dict:
    """Luật/Bộ luật/Pháp lệnh dẫn theo tên và ngày thông qua ("Luật Ngân sách nhà nước ngày 25 tháng 6 năm 2025")."""
    type_slug = re.sub(r"[^a-z0-9]+", "-", ascii_fold(doc_type).lower()).strip("-")
    title_slug = re.sub(r"[^a-z0-9]+", "-", ascii_fold(title).lower()).strip("-")
    if not type_slug or len(title_slug) < 4:
        return {"status": "not_found"}
    try:
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT url, num_year FROM legal_pages WHERE scope = 'central' AND slug ~ %s LIMIT 20",
                           (rf"^{re.escape(type_slug)}-{re.escape(title_slug)}-so-\d+[a-z]?-\d{{4}}-",))
            rows = cursor.fetchall()
    except Exception as exc:
        return {"status": "unavailable", "message": f"Chưa có danh mục văn bản pháp luật ({type(exc).__name__})."}
    # Số luật mang năm thông qua; ưu tiên đúng năm, sau đó năm gần nhất trước đó.
    rows.sort(key=lambda row: (int((row[1] or "0/0").split("/")[1]) != issued.year,
                               abs(int((row[1] or "0/0").split("/")[1]) - issued.year)))
    own = client is None
    client = client or _client()
    first = None
    try:
        for url, _ in rows[:2]:
            with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("SELECT * FROM legal_documents WHERE url = %s "
                               "AND fetched_at > now() - make_interval(days => %s)", (url, CACHE_DAYS))
                cached = cursor.fetchone()
            record = dict(cached) if cached else None
            if record is None:
                data = _legislation(client, url)
                if not data:
                    continue
                record = _save(url, data)
            issued_found = record.get("issued")
            if hasattr(issued_found, "isoformat"):
                issued_found = issued_found.isoformat()
            if issued_found == issued.isoformat():
                return _result(record)
            first = first or record
        # Có luật cùng tên nhưng ngày thông qua khác: trả về để báo sai ngày.
        return {**_result(first), "date_mismatch": True} if first else {"status": "not_found"}
    except httpx.HTTPError as exc:
        return {"status": "unavailable", "message": f"Không kết nối được vbpl.vn ({type(exc).__name__})."}
    finally:
        if own:
            client.close()


def _result(record: dict, same_number: int = 1) -> dict:
    level, label = _FORCE.get(record.get("legal_force") or "", ("info", "Chưa rõ tình trạng hiệu lực"))
    issued = record.get("issued")
    return {"status": "found", "url": record["url"], "identifier": record.get("identifier"),
            "doc_type": record.get("doc_type"), "issuer": record.get("issuer"), "name": record.get("name"),
            "issued": issued.isoformat() if hasattr(issued, "isoformat") else issued,
            "legal_force": record.get("legal_force"), "force_level": level, "force_label": label,
            "same_number": same_number}


def open_client() -> httpx.Client:
    return _client()
