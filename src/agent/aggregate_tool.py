"""Công cụ tổng hợp số liệu cho agent: chạy Aggregation v2 và trình bày kết quả có minh chứng."""
from __future__ import annotations

import logging
import time
from typing import Callable, Iterator

from src.agent.schemas import AgentResult, QueryPlan
from src.services import aggregate_srv
from src.services.numbers import format_number

logger = logging.getLogger(__name__)

_MAX_LISTED = 20
_REVIEW_LABELS = {
    "trich_dan_khong_khop_nguon": "trích dẫn không khớp nguồn",
    "so_khong_co_trong_trich_dan": "số không có trong trích dẫn",
    "khong_ro_doan_nguon": "không rõ đoạn nguồn",
    "khong_doc_duoc_so": "không đọc được số",
    "do_tin_cay_thap": "độ tin cậy thấp",
    "dau_phan_cach_mo_ho": "dấu phân cách mơ hồ",
    "trung_lap": "trùng lặp",
    "trung_lap_giua_van_ban": "trùng giữa các văn bản",
    "chi_tiet_da_co_dong_tong": "dòng chi tiết (đã dùng dòng tổng)",
    "khac_don_vi": "khác đơn vị",
    "gia_tri_khong_hop_le": "giá trị không phải số hợp lệ",
    "trich_dan_thieu_ngu_canh": "trích dẫn thiếu ngữ cảnh",
}


def review_label(reason: str) -> str:
    if reason.startswith("khac_trang_thai_"):
        return "khác trạng thái (" + reason.removeprefix("khac_trang_thai_").replace("_", " ") + ")"
    return _REVIEW_LABELS.get(reason, reason)


def render_answer(data: dict) -> tuple[str, list[dict]]:
    """Markdown answer with [E#] citations and the matching source list."""
    included = data.get("included") or []
    sources, lines = [], []
    for index, fact in enumerate(included[:_MAX_LISTED], start=1):
        evidence_id = f"E{index}"
        sources.append({
            "text": fact.get("quote") or "",
            "metadata": {
                "evidence_id": evidence_id, "doc_id": fact.get("doc_id"), "so_ky_hieu": fact.get("so_ky_hieu"),
                "ngay_ban_hanh": fact.get("ngay_ban_hanh"), "file_name": fact.get("file_name"),
                "value": fact.get("value"), "unit": fact.get("unit"), "status": fact.get("status"),
                "source_url": fact.get("source_url"), "retrieval_tool": "aggregate_documents",
            },
            "score": float(fact.get("confidence") or 1.0),
        })
        if data.get("operation") == "count_documents":
            date = str(fact.get("ngay_ban_hanh") or "")[:10]
            lines.append(f"- **{fact.get('so_ky_hieu') or '?'}**" + (f" ({date})" if date else "") +
                         f": {fact.get('label') or 'không có trích yếu'} [{evidence_id}]")
            continue
        value = format_number(fact.get("value"), fact.get("unit"))
        label = f" – {fact['label']}" if fact.get("label") else ""
        lines.append(f"- **{fact.get('so_ky_hieu') or '?'}**{label}: {value} [{evidence_id}]")

    if not included:
        answer = "Không tìm thấy số liệu phù hợp trong phạm vi văn bản đã quét."
    else:
        answer = f"**{aggregate_srv.summarize(data)}**"
        if data.get("operation") == "count_documents":
            answer += f" — {aggregate_srv.describe_scope(data.get('plan') or {})}."
        else:
            answer += (f" — từ {data.get('n_docs_with_facts', 0)} văn bản có số liệu "
                       f"(đã quét {data.get('n_docs_scanned', 0)}/{data.get('n_docs_in_scope', 0)} văn bản trong phạm vi).")
        if data.get("groups"):
            answer += "\n\n| Nhóm | Giá trị | Số văn bản |\n|---|---:|---:|\n" + "\n".join(
                f"| {row['key']} | {format_number(row['value'], data.get('unit'))} | {row['n_docs']} |" for row in data["groups"])
        heading = "Danh sách văn bản" if data.get("operation") == "count_documents" else "Minh chứng"
        answer += f"\n\n{heading}:\n" + "\n".join(lines)
        if len(included) > _MAX_LISTED:
            answer += f"\n- … và {len(included) - _MAX_LISTED} dữ kiện khác (xem bảng tổng hợp)."
    review = data.get("review") or []
    if review:
        answer += f"\n\n{len(review)} dữ kiện chưa được cộng vì cần cán bộ xác nhận (ví dụ: {review_label(review[0]['review_reason'])})."
    for warning in data.get("warnings") or []:
        answer += f"\n\nLưu ý: {warning}"
    answer += ("\n\nĐây là tổng hợp tự động: số được tính bằng code từ trích dẫn nguyên văn, nhưng cần kiểm tra "
               "đơn vị, kỳ báo cáo và phạm vi trước khi dùng cho báo cáo chính thức.")
    return answer, sources


def aggregate_events(query: str, plan: QueryPlan, started: float, explicit_filters: dict,
                     finish: Callable[[AgentResult], Iterator[dict]]) -> Iterator[dict]:
    data, error = None, None
    try:
        for event in aggregate_srv.aggregate_events(query, explicit_filters):
            if event["type"] == "aggregation_result":
                data = event["data"]
            else:
                yield event
        answer, sources = render_answer(data)
        confidence = "trung_binh" if data.get("included") and not data.get("warnings") else "thap"
    except Exception as exc:
        logger.exception("Aggregation failed")
        error = f"{type(exc).__name__}: {exc}"
        sources = []
        answer = ("Không hoàn tất được việc tổng hợp do lỗi hoặc quá thời gian xử lý. "
                  "Không được coi đây là kết quả bằng 0.")
        confidence = "thap"
    yield {"type": "sources", "sources": sources}
    yield {"type": "token", "text": answer}
    yield {"type": "answer", "answer": answer, "verified": error is None, "confidence": confidence, "changed": False}
    result = AgentResult(
        query=query, answer=answer, sources=sources, confidence=confidence, attempts=1, plan=plan,
        route="aggregate", error=error, verified=error is None, aggregation=data,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    yield from finish(result)
