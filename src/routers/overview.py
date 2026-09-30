"""Số liệu tổng quan công khai cho trang chủ (chỉ số đếm tổng hợp, không có nội dung văn bản)."""
import asyncio
import logging
import threading
import time
from datetime import date

import psycopg2.extras
from fastapi import APIRouter

from src.core import config, store
from src.crawler.sso import crawler_state

logger = logging.getLogger(__name__)
router = APIRouter()

TYPE_LABELS = {
    "nghi_quyet": "Nghị quyết", "quyet_dinh": "Quyết định", "chi_thi": "Chỉ thị", "quy_che": "Quy chế",
    "quy_dinh": "Quy định", "thong_cao": "Thông cáo", "thong_bao": "Thông báo", "huong_dan": "Hướng dẫn",
    "chuong_trinh": "Chương trình", "ke_hoach": "Kế hoạch", "phuong_an": "Phương án", "de_an": "Đề án",
    "du_an": "Dự án", "bao_cao": "Báo cáo", "bien_ban": "Biên bản", "to_trinh": "Tờ trình",
    "hop_dong": "Hợp đồng", "cong_van": "Công văn", "cong_dien": "Công điện", "ban_ghi_nho": "Bản ghi nhớ",
    "ban_thoa_thuan": "Bản thỏa thuận", "giay_uy_quyen": "Giấy ủy quyền", "giay_moi": "Giấy mời",
    "giay_gioi_thieu": "Giấy giới thiệu", "giay_nghi_phep": "Giấy nghỉ phép", "phieu_gui": "Phiếu gửi",
    "phieu_chuyen": "Phiếu chuyển", "phieu_bao": "Phiếu báo", "thu_cong": "Thư công", "khac": "Khác",
}
_CACHE_SECONDS = 10
_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def _months(count: int = 12) -> list[str]:
    today = date.today()
    year, month = today.year, today.month
    output = []
    for _ in range(count):
        output.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(output))


def _collect() -> dict:
    ready = "d.ingest_status = 'ready' AND d.index_version = %s"
    version = (config.INGEST_VERSION,)
    data: dict = {"generated_at": time.time()}
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(f"SELECT count(*) AS docs, COALESCE(sum(n_chunks), 0) AS chunks, "
                       f"COALESCE(sum(n_files), 0) AS files FROM documents d WHERE {ready}", version)
        data["documents"] = {key: int(value) for key, value in cursor.fetchone().items()}
        cursor.execute(f"SELECT COALESCE(d.loai_vb, 'khac') AS code, count(*) AS n FROM documents d WHERE {ready} "
                       "GROUP BY 1 ORDER BY n DESC", version)
        rows = cursor.fetchall()
        top, other = rows[:7], sum(int(row["n"]) for row in rows[7:])
        data["by_type"] = [{"label": TYPE_LABELS.get(row["code"], row["code"]), "n": int(row["n"])} for row in top]
        if other:
            data["by_type"].append({"label": "Loại khác", "n": other})
        months = _months()
        cursor.execute(f"SELECT to_char(d.ngay_ban_hanh, 'YYYY-MM') AS month, count(*) AS n FROM documents d "
                       f"WHERE {ready} AND d.ngay_ban_hanh >= %s GROUP BY 1", (*version, months[0] + "-01"))
        counts = {row["month"]: int(row["n"]) for row in cursor.fetchall()}
        data["by_month"] = [{"month": month, "n": counts.get(month, 0)} for month in months]
        cursor.execute(f"SELECT COALESCE(d.huong, 'khac') AS huong, count(*) AS n FROM documents d WHERE {ready} "
                       "GROUP BY 1", version)
        data["by_direction"] = {row["huong"]: int(row["n"]) for row in cursor.fetchall()}
        cursor.execute("""SELECT date_trunc('hour', created_at) AS hour, count(*) AS n
                          FROM rag_query_logs WHERE created_at > now() - interval '24 hours' GROUP BY 1""")
        hours = {row["hour"].strftime("%Y-%m-%dT%H"): int(row["n"]) for row in cursor.fetchall()}
        cursor.execute("""SELECT to_char(date_trunc('hour', now()) - (g * interval '1 hour'), 'YYYY-MM-DD"T"HH24') AS h
                          FROM generate_series(23, 0, -1) AS g""")
        data["queries_24h"] = [{"hour": row["h"], "n": hours.get(row["h"], 0)} for row in cursor.fetchall()]
        cursor.execute("""SELECT count(*) AS n,
                                 percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
                                 percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
                          FROM rag_query_logs WHERE created_at > now() - interval '7 days' AND latency_ms > 0""")
        row = cursor.fetchone()
        data["latency_7d"] = {"queries": int(row["n"]),
                              "p50_ms": int(row["p50"]) if row["p50"] is not None else None,
                              "p95_ms": int(row["p95"]) if row["p95"] is not None else None}
    try:
        from src.crawler import qlvb_api
        summary = qlvb_api.summary()
        data["sync"] = {direction: {key: summary[direction].get(key) for key in
                                    ("label", "source_documents", "inventoried", "done", "pending", "failed", "skipped")}
                        for direction in ("di", "den")}
    except Exception as exc:
        logger.warning("Không đọc được tiến độ đồng bộ: %s", exc)
        data["sync"] = None
    try:
        data["documents"]["vectors"] = store._q_stats.get_collection(config.RAG_COLLECTION).points_count
    except Exception:
        data["documents"]["vectors"] = None
    progress = crawler_state.get("progress") or {}
    data["crawler"] = {"status": crawler_state.get("status"), "job": crawler_state.get("job"),
                       "done": progress.get("done"), "total": progress.get("total"), "phase": progress.get("phase")}
    return data


def overview_data() -> dict:
    with _lock:
        if _cache["data"] is not None and time.monotonic() - _cache["at"] < _CACHE_SECONDS:
            return _cache["data"]
    data = _collect()
    with _lock:
        _cache.update(at=time.monotonic(), data=data)
    return data


@router.get("/overview")
async def api_overview():
    """Chỉ số tổng hợp cho biểu đồ trang chủ; làm mới tối đa mỗi 10 giây."""
    try:
        return await asyncio.to_thread(overview_data)
    except Exception:
        logger.exception("Không tổng hợp được số liệu tổng quan")
        return {"generated_at": time.time(), "error": "Không đọc được số liệu."}
