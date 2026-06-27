"""TỔNG HỢP qua nhiều văn bản (ví dụ: 'tổng số buổi tập huấn CĐS trong tất cả
báo cáo'). Nguyên tắc: KHÔNG để LLM tự cộng. Trích từng văn bản -> code cộng.

Luồng:
  1. plan: LLM -> {metric, loai_vb, keywords, date_from, date_to}
  2. chọn văn bản ứng viên (Postgres filter + hybrid retrieval thu hẹp)
  3. map: mỗi văn bản -> LLM rẻ trích {found, value, evidence}
  4. reduce: code cộng + trả bảng minh chứng
"""
from src.core import llm, store, config
from src.services import search_srv as search

_PLAN_SYS = "Phân tích câu hỏi tổng hợp của chuyên viên nhà nước thành kế hoạch truy vấn."
_PLAN_FMT = """Trả JSON:
{"metric": "mô tả ngắn chỉ số cần đếm/cộng",
 "agg": "sum" | "count",
 "loai_vb": "bao_cao"|"cong_van"|...|null,
 "keywords": "vài từ khóa để lọc văn bản liên quan",
 "date_from": "YYYY-MM-DD"|null, "date_to": "YYYY-MM-DD"|null}"""

_MAP_SYS = """Trích MỘT con số từ văn bản theo yêu cầu. Tuyệt đối không suy đoán.
Nếu văn bản không nêu con số đó, found=false."""


def _plan(question: str) -> dict:
    return llm.extract_json(_PLAN_SYS, f"CÂU HỎI: {question}\n\n{_PLAN_FMT}",
                            model=config.LLM_CHEAP) or {}


def _candidate_docs(plan: dict, max_docs: int = 60):
    conds, params = [], []
    if plan.get("loai_vb"):
        conds.append("loai_vb = %s"); params.append(plan["loai_vb"])
    if plan.get("date_from"):
        conds.append("ngay_ban_hanh >= %s"); params.append(plan["date_from"])
    if plan.get("date_to"):
        conds.append("ngay_ban_hanh <= %s"); params.append(plan["date_to"])
    where = " AND ".join(conds)
    docs = store.get_documents(where, tuple(params), limit=max_docs)

    # thu hẹp thêm bằng hybrid retrieval theo keyword (nếu quá nhiều)
    if plan.get("keywords") and len(docs) > 15:
        hits = search.retrieve(plan["keywords"], top_k=40, rerank_pool=60)
        keep = {h["payload"].get("doc_id") for h in hits}
        narrowed = [d for d in docs if d["id"] in keep]
        if narrowed:
            docs = narrowed
    return docs


def _map_one(metric: str, doc: dict) -> dict:
    body = (doc.get("full_text") or "")[:8000]
    user = (f'CHỈ SỐ CẦN TRÍCH: {metric}\n\nVĂN BẢN:\n{body}\n\n'
            'Trả JSON: {"found": bool, "value": number|null, '
            '"unit": str|null, "evidence": "trích nguyên văn câu chứa số"}')
    r = llm.extract_json(_MAP_SYS, user, model=config.LLM_CHEAP) or {}
    return r


def aggregate(question: str) -> dict:
    plan = _plan(question)
    metric = plan.get("metric", question)
    agg = plan.get("agg", "sum")
    docs = _candidate_docs(plan)

    rows, total = [], 0
    for d in docs:
        r = _map_one(metric, d)
        if not r.get("found"):
            continue
        val = r.get("value") or 0
        if agg == "count":
            val = 1
        try:
            total += float(val)
        except Exception:
            continue
        rows.append({
            "so_ky_hieu": d.get("so_ky_hieu"),
            "ngay_ban_hanh": str(d.get("ngay_ban_hanh")),
            "value": r.get("value"), "unit": r.get("unit"),
            "evidence": r.get("evidence"),
        })

    return {
        "question": question, "metric": metric, "agg": agg,
        "total": total, "n_docs_scanned": len(docs), "n_docs_matched": len(rows),
        "evidence": rows,   # luôn kèm minh chứng từng văn bản
    }
