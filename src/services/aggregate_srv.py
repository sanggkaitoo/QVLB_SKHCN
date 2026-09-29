"""Aggregation v2: tổng hợp số liệu qua nhiều văn bản.

Nguyên tắc: LLM chỉ là bộ TRÍCH XUẤT dữ kiện có trích dẫn; mọi phép tính do code thực hiện.

  1. Kế hoạch có kiểu: chỉ số, phép tính, đơn vị, trạng thái (kế hoạch/thực hiện/lũy kế), phạm vi, group_by.
  2. PostgreSQL liệt kê đầy đủ văn bản trong phạm vi (phân trang, giới hạn cấu hình, báo rõ khi bị cắt).
  3. Hybrid search theo nhóm văn bản lấy các đoạn liên quan trong toàn văn bản (không cắt 8.000 ký tự đầu);
     reranker loại văn bản không liên quan.
  4. Extractor trả NHIỀU fact/văn bản, mỗi fact gắn đoạn nguồn, trích dẫn nguyên văn, trạng thái, confidence.
  5. Chuẩn hóa số (dấu phân cách, nghìn/triệu/tỷ, %), kiểm tra trích dẫn có thật trong đoạn nguồn.
  6. Chống trùng: dòng tổng vs dòng chi tiết, fact lặp giữa các đoạn/văn bản.
  7. Tính sum/count/avg/min/max/distinct_count, nhóm theo văn bản/cơ quan/tháng/năm/loại.
  8. Kiểm tra bất biến (tổng chi tiết = dòng tổng, một đơn vị); fact nghi vấn đưa vào danh sách cần xác nhận.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
import contextvars
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field

from src.agent.query_analyzer import detect_filters
from src.core import config, embedder, llm, store
from src.core.runtime import budget, remaining
from src.services import retrieval_srv
from src.services.document_fields import normalize_filters
from src.services.numbers import digits_of, format_number, normalize_quantity

logger = logging.getLogger(__name__)

Operation = Literal["sum", "count", "count_documents", "avg", "min", "max", "distinct_count"]
Status = Literal["ke_hoach", "thuc_hien", "luy_ke", "bat_ky"]
GroupBy = Literal["none", "document", "co_quan_ban_hanh", "thang", "nam", "loai_vb"]

_COUNT_DOCS_RE = re.compile(r"\b(bao nhiêu|số lượng|đếm( số)?)\s+(văn bản|công văn|quyết định|kế hoạch|báo cáo|"
                            r"tờ trình|thông báo|giấy mời|nghị quyết|chỉ thị|hướng dẫn)\b", re.IGNORECASE)
_OPERATION_WORDS = (("trung bình", "avg"), ("lớn nhất", "max"), ("cao nhất", "max"), ("nhỏ nhất", "min"),
                    ("thấp nhất", "min"))
_STATUS_LABELS = {"ke_hoach": "kế hoạch", "thuc_hien": "thực hiện", "luy_ke": "lũy kế", "bat_ky": "mọi trạng thái"}
_OPERATION_LABELS = {"sum": "Tổng", "count": "Số lượng dữ kiện", "count_documents": "Số văn bản", "avg": "Trung bình",
                     "min": "Nhỏ nhất", "max": "Lớn nhất", "distinct_count": "Số đối tượng khác nhau"}

_PLAN_SYSTEM = """Phân tích câu hỏi tổng hợp số liệu của chuyên viên nhà nước thành kế hoạch có cấu trúc.
Không trả lời câu hỏi, không tự tính số."""
_PLAN_FORMAT = """Trả JSON:
{"metric": "mô tả ngắn chỉ số cần đếm/cộng (ví dụ: số buổi tập huấn chuyển đổi số)",
 "operation": "sum|count|count_documents|avg|min|max|distinct_count",
 "unit_hint": "đơn vị mong đợi hoặc null",
 "group_by": "none|document|co_quan_ban_hanh|thang|nam|loai_vb",
 "keywords": "vài từ khóa để tìm đoạn chứa số liệu (một chuỗi)"}
count_documents: đếm số văn bản thỏa điều kiện (không cần đọc số liệu bên trong).
count: đếm số dữ kiện/đối tượng được nêu trong văn bản."""

_EXTRACT_SYSTEM = """Bạn trích DỮ KIỆN SỐ từ các đoạn văn bản hành chính theo chỉ số được yêu cầu.
Tuyệt đối không suy đoán, không tự cộng hay tính toán, không đổi đơn vị.
Mỗi dữ kiện phải có trích dẫn NGUYÊN VĂN (quote) chứa đúng con số, lấy từ một đoạn C1, C2...
Với bảng: mỗi dòng liên quan là một dữ kiện; dòng "Tổng/Cộng/Tổng cộng" đánh dấu is_total=true.
status: ke_hoach (chỉ tiêu, dự kiến, kế hoạch), thuc_hien (đã thực hiện, kết quả), luy_ke (lũy kế, cộng dồn), khong_ro.
Bỏ qua: số thứ tự, số hiệu cột/công thức (ví dụ "9=4+8"), mã số, ngày tháng, số ký hiệu văn bản,
ô trống hoặc biểu mẫu chưa điền, và chuỗi ký tự lỗi nhận dạng.
Tối đa 30 dữ kiện, ưu tiên dòng tổng. Nếu văn bản không nêu chỉ số, trả facts rỗng."""
_EXTRACT_FORMAT = """Trả JSON:
{"facts": [{"chunk": "C1", "label": "đối tượng/dòng số liệu", "value_text": "số nguyên văn, ví dụ 1.234,5",
            "unit_text": "đơn vị nguyên văn hoặc null", "status": "ke_hoach|thuc_hien|luy_ke|khong_ro",
            "period": "kỳ báo cáo nếu có hoặc null", "is_total": false,
            "quote": "câu/dòng nguyên văn chứa số", "confidence": 0.9}]}"""


class AggregationPlan(BaseModel):
    metric: str
    operation: Operation = "sum"
    unit_hint: str | None = None
    status: Status = "bat_ky"
    group_by: GroupBy = "none"
    keywords: str = ""
    filters: dict[str, Any] = Field(default_factory=dict)
    planned_by: str = "rules"


_STATUS_RULES = (("luy_ke", re.compile(r"\b(lũy kế|luỹ kế|cộng dồn)\b", re.IGNORECASE)),
                 ("ke_hoach", re.compile(r"\b(kế hoạch giao|chỉ tiêu|dự kiến|dự toán|theo kế hoạch)\b", re.IGNORECASE)),
                 ("thuc_hien", re.compile(r"\b(đã thực hiện|thực hiện được|kết quả thực hiện|đã tổ chức|đã giải ngân|thực tế)\b", re.IGNORECASE)))
_TOPIC_RE = re.compile(r"\b(?:về|liên quan (?:đến|tới)|chủ đề)\s+(.+?)(?=\s+(?:trong|năm|của|do|từ|được|đã|thuộc)\b|[?.!]|$)",
                       re.IGNORECASE)


def rule_status(question: str) -> str:
    """Trạng thái chỉ lấy theo từ ngữ trong câu hỏi; không để model tự giới hạn."""
    for status, pattern in _STATUS_RULES:
        if pattern.search(question):
            return status
    return "bat_ky"


def document_topic(question: str) -> str:
    """Chủ đề cần lọc khi đếm văn bản ('bao nhiêu kế hoạch về chuyển đổi số' -> 'chuyển đổi số')."""
    match = _TOPIC_RE.search(question)
    return match.group(1).strip() if match else ""


def _rule_operation(question: str) -> str:
    lowered = question.lower()
    if _COUNT_DOCS_RE.search(lowered):
        return "count_documents"
    for word, operation in _OPERATION_WORDS:
        if word in lowered:
            return operation
    return "sum"


def plan_aggregation(question: str, explicit_filters: dict | None = None) -> AggregationPlan:
    # Normalize first: empty query parameters (None) must not erase rule-detected filters.
    rule_filters = normalize_filters({**detect_filters(question), **normalize_filters(explicit_filters)})
    base = AggregationPlan(metric=question, operation=_rule_operation(question), keywords=question,
                           status=rule_status(question), filters=rule_filters)
    if base.operation == "count_documents":
        # Counting documents needs no model: filters come from rules, relevance only for an explicit topic.
        base.keywords = document_topic(question)
        return base
    try:
        planned = llm.extract_json(_PLAN_SYSTEM, f"CÂU HỎI: {question}\n\n{_PLAN_FORMAT}", model=config.LLM_CHEAP,
                                   timeout=config.LLM_FAST_TIMEOUT_SECONDS, max_tokens=600)
    except Exception as exc:
        logger.warning("Planner tổng hợp lỗi, dùng kế hoạch theo luật: %s", exc)
        return base
    if not isinstance(planned, dict):
        return base
    try:
        keywords = planned.get("keywords") or ""
        if isinstance(keywords, list):
            keywords = " ".join(str(value) for value in keywords)
        plan = AggregationPlan(
            metric=str(planned.get("metric") or question)[:300],
            operation=planned.get("operation") if planned.get("operation") in Operation.__args__ else base.operation,
            unit_hint=(str(planned["unit_hint"])[:40] if planned.get("unit_hint") else None),
            status=base.status,
            group_by=planned.get("group_by") if planned.get("group_by") in GroupBy.__args__ else "none",
            keywords=str(keywords).strip("[]'\" ")[:300],
            filters=rule_filters,
            planned_by="llm",
        )
        if plan.operation == "count_documents":
            plan.operation = "sum"  # only explicit "bao nhiêu văn bản" wording (handled above) counts documents
        return plan
    except Exception as exc:
        logger.warning("Kế hoạch tổng hợp không hợp lệ: %s", exc)
        return base


def _relevant_documents(plan: AggregationPlan, documents: list[dict]) -> dict[int, list[dict]]:
    """doc_id -> relevant chunks (best first), keeping only documents that pass the rerank gate."""
    if not documents:
        return {}
    query = " ".join(value for value in (plan.metric, plan.keywords) if value)
    vector = embedder.encode_one(query)
    flt = retrieval_srv.qdrant_filter(doc_ids=[doc["id"] for doc in documents], kinds=["child", "document_summary"])
    groups = store.hybrid_query_groups(vector["dense"], vector["sparse"], group_by="doc_id", limit=len(documents),
                                       group_size=config.AGG_CHUNKS_PER_DOC, flt=flt)
    chunks_by_doc: dict[int, list[dict]] = {}
    for group in groups:
        items = [retrieval_srv._item(hit) for hit in group.hits]
        if items:
            chunks_by_doc[int(group.id)] = items
    # Rerank the two best chunks of every document once; documents below the gate are not read by the LLM.
    pairs, owners = [], []
    for doc_id, items in chunks_by_doc.items():
        for item in items[:2]:
            pairs.append((query, retrieval_srv.rerank_passage(item)))
            owners.append(doc_id)
    scores = embedder.rerank_pairs(pairs)
    best: dict[int, float] = defaultdict(float)
    for doc_id, score in zip(owners, scores):
        best[doc_id] = max(best[doc_id], score)
    relevant = {doc_id: chunks_by_doc[doc_id] for doc_id, score in best.items() if score >= config.AGG_MIN_SCORE}
    for doc_id in relevant:
        for item in relevant[doc_id]:
            item["doc_score"] = best[doc_id]
    return relevant


def _chunk_label(index: int, item: dict) -> str:
    payload = item["payload"]
    parts = [f"C{index}"]
    if payload.get("file_role") == "dinh_kem":
        parts.append(f"tệp đính kèm {payload.get('file_name')}")
    if payload.get("section_path"):
        parts.append(str(payload["section_path"])[:80])
    if payload.get("page_start"):
        parts.append(f"trang {payload['page_start']}")
    return " | ".join(parts)


def _extract_facts(plan: AggregationPlan, document: dict, chunks: list[dict]) -> list[dict]:
    context = "\n\n".join(f"[{_chunk_label(index, item)}]\n{item['text']}" for index, item in enumerate(chunks, start=1))
    user = (f"CHỈ SỐ CẦN TRÍCH: {plan.metric}\nTrạng thái cần: {_STATUS_LABELS[plan.status]}\n"
            f"VĂN BẢN: {document.get('so_ky_hieu') or '?'} – {document.get('trich_yeu') or ''}\n\n"
            f"CÁC ĐOẠN:\n{context}\n\n{_EXTRACT_FORMAT}")
    result = llm.extract_json(_EXTRACT_SYSTEM, user, model=config.LLM_CHEAP,
                              timeout=config.LLM_FAST_TIMEOUT_SECONDS * 2, max_tokens=3500)
    facts = result.get("facts") if isinstance(result, dict) else None
    return [fact for fact in facts or [] if isinstance(fact, dict)][:40]


def _normalize_ws(text: str) -> str:
    return " ".join(str(text or "").lower().split())


# Một con số (có thể kèm dấu, phân cách, %, hệ số/đơn vị bằng chữ) — loại công thức cột "9=4+8", chuỗi OCR lỗi.
_VALUE_TEXT_RE = re.compile(r"[-−]?\(?\d[\d.,\s]*\)?\s*(%|[^\W\d_][^\W\d_ .]*(\s+[^\W\d_][^\W\d_.]*)?)?\.?")


def _prepare_fact(raw: dict, document: dict, chunks: list[dict]) -> dict:
    match = re.fullmatch(r"C(\d+)", str(raw.get("chunk") or "").strip().upper())
    chunk = chunks[int(match.group(1)) - 1] if match and 0 < int(match.group(1)) <= len(chunks) else None
    quantity = normalize_quantity(str(raw.get("value_text") or ""), raw.get("unit_text"))
    quote = str(raw.get("quote") or "").strip()[:600]
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.5
    payload = (chunk or {}).get("payload") or {}
    fact = {
        "doc_id": int(document["id"]),
        "so_ky_hieu": document.get("so_ky_hieu"),
        "ngay_ban_hanh": str(document.get("ngay_ban_hanh") or ""),
        "co_quan_ban_hanh": document.get("co_quan_ban_hanh"),
        "loai_vb": document.get("loai_vb"),
        "source_url": document.get("source_url"),
        "file_name": payload.get("file_name"),
        "file_role": payload.get("file_role"),
        "section_path": payload.get("section_path"),
        "page": payload.get("page_start"),
        "label": " ".join(str(raw.get("label") or "").split())[:200],
        "value_text": str(raw.get("value_text") or "")[:60],
        "unit_text": str(raw.get("unit_text") or "")[:40] or None,
        "value": quantity["value"],
        "unit": quantity["unit"],
        "kind": quantity["kind"],
        "status": raw.get("status") if raw.get("status") in {"ke_hoach", "thuc_hien", "luy_ke"} else "khong_ro",
        "period": (str(raw.get("period"))[:60] if raw.get("period") else None),
        "is_total": bool(raw.get("is_total")),
        "quote": quote,
        "confidence": confidence,
        "issues": [],
    }
    if chunk is None:
        fact["issues"].append("khong_ro_doan_nguon")
    elif _normalize_ws(quote) not in _normalize_ws(chunk["text"]):
        fact["issues"].append("trich_dan_khong_khop_nguon")
    if quantity["value"] is None:
        fact["issues"].append("khong_doc_duoc_so")
    elif not _VALUE_TEXT_RE.fullmatch(fact["value_text"].strip()):
        fact["issues"].append("gia_tri_khong_hop_le")
    elif not re.search(r"[^\W\d_]{2,}", quote):
        fact["issues"].append("trich_dan_thieu_ngu_canh")
    elif digits_of(fact["value_text"]) not in digits_of(quote):
        fact["issues"].append("so_khong_co_trong_trich_dan")
    if quantity["ambiguous"]:
        fact["issues"].append("dau_phan_cach_mo_ho")
    return fact


def _review(fact: dict, reason: str, review: list[dict]) -> None:
    fact = dict(fact)
    fact["review_reason"] = reason
    review.append(fact)


def _select_facts(plan: AggregationPlan, facts: list[dict], warnings: list[str]) -> tuple[list[dict], list[dict]]:
    included, review = [], []
    seen_quotes: dict[tuple, int] = {}
    by_doc: dict[int, list[dict]] = defaultdict(list)
    for fact in facts:
        blocking = [issue for issue in fact["issues"] if issue != "dau_phan_cach_mo_ho"]
        if blocking:
            _review(fact, blocking[0], review)
            continue
        if fact["confidence"] < config.AGG_MIN_CONFIDENCE or "dau_phan_cach_mo_ho" in fact["issues"]:
            _review(fact, "do_tin_cay_thap" if fact["confidence"] < config.AGG_MIN_CONFIDENCE else "dau_phan_cach_mo_ho", review)
            continue
        if plan.status != "bat_ky" and fact["status"] not in {plan.status, "khong_ro"}:
            _review(fact, "khac_trang_thai_" + fact["status"], review)
            continue
        key = (round(fact["value"], 6), fact["unit"], _normalize_ws(fact["quote"]))
        if key in seen_quotes:
            _review(fact, "trung_lap" if seen_quotes[key] == fact["doc_id"] else "trung_lap_giua_van_ban", review)
            continue
        seen_quotes[key] = fact["doc_id"]
        by_doc[fact["doc_id"]].append(fact)

    for doc_id, doc_facts in by_doc.items():
        totals = [fact for fact in doc_facts if fact["is_total"]]
        details = [fact for fact in doc_facts if not fact["is_total"]]
        if totals and details:
            # Avoid double counting: the document's total row replaces its detail rows.
            same_unit = [fact for fact in details if fact["unit"] == totals[0]["unit"]]
            detail_sum = sum(fact["value"] for fact in same_unit)
            if same_unit and abs(detail_sum - totals[0]["value"]) > max(1e-6, abs(totals[0]["value"]) * 0.01):
                warnings.append(f"{totals[0]['so_ky_hieu']}: tổng các dòng chi tiết ({format_number(detail_sum, totals[0]['unit'])}) "
                                f"khác dòng tổng ({format_number(totals[0]['value'], totals[0]['unit'])}).")
            for fact in details:
                _review(fact, "chi_tiet_da_co_dong_tong", review)
            included.extend(totals)
        else:
            included.extend(doc_facts)

    if plan.status == "bat_ky":
        statuses = {fact["status"] for fact in included} - {"khong_ro"}
        if len(statuses) > 1:
            warnings.append("Số liệu gồm nhiều trạng thái (" + ", ".join(_STATUS_LABELS[s] for s in sorted(statuses)) +
                            "); nên hỏi rõ kế hoạch hay thực hiện để tránh cộng lẫn.")
    if any(fact["status"] == "khong_ro" for fact in included) and plan.status != "bat_ky":
        warnings.append("Một số dữ kiện không ghi rõ trạng thái kế hoạch/thực hiện.")

    if plan.operation in {"sum", "avg", "min", "max"}:
        kinds = Counter((fact["kind"], fact["unit"]) for fact in included)
        if kinds:
            preferred = None
            if plan.unit_hint:
                hint = normalize_quantity("1", plan.unit_hint)
                preferred = next(((kind, unit) for kind, unit in kinds if (kind, unit) == (hint["kind"], hint["unit"])), None)
            target = preferred or kinds.most_common(1)[0][0]
            if plan.operation == "sum" and target[0] == "percent":
                warnings.append("Không cộng các tỷ lệ phần trăm; hãy dùng trung bình, lớn nhất hoặc nhỏ nhất.")
            kept = []
            for fact in included:
                if (fact["kind"], fact["unit"]) == target:
                    kept.append(fact)
                else:
                    _review(fact, "khac_don_vi", review)
            included = kept
    return included, review


def _group_key(fact: dict, group_by: str) -> str:
    if group_by == "document":
        return fact.get("so_ky_hieu") or f"doc {fact['doc_id']}"
    if group_by == "co_quan_ban_hanh":
        return fact.get("co_quan_ban_hanh") or "Không rõ cơ quan"
    if group_by == "thang":
        return (fact.get("ngay_ban_hanh") or "")[:7] or "Không rõ ngày"
    if group_by == "nam":
        return (fact.get("ngay_ban_hanh") or "")[:4] or "Không rõ ngày"
    if group_by == "loai_vb":
        return fact.get("loai_vb") or "khac"
    return "Tất cả"


def _reduce(operation: str, facts: list[dict]) -> float | None:
    if operation == "count":
        return float(len(facts))
    if operation == "distinct_count":
        return float(len({_normalize_ws(fact["label"]) for fact in facts if fact["label"]}))
    values = [fact["value"] for fact in facts if fact["value"] is not None]
    if not values:
        return None
    if operation == "sum":
        return float(sum(values))
    if operation == "avg":
        return float(sum(values) / len(values))
    if operation == "min":
        return float(min(values))
    if operation == "max":
        return float(max(values))
    return None


def _document_row(document: dict, item: dict | None) -> dict:
    return {
        "doc_id": int(document["id"]), "so_ky_hieu": document.get("so_ky_hieu"),
        "ngay_ban_hanh": str(document.get("ngay_ban_hanh") or ""), "co_quan_ban_hanh": document.get("co_quan_ban_hanh"),
        "loai_vb": document.get("loai_vb"), "source_url": document.get("source_url"),
        "label": document.get("trich_yeu"), "value": 1.0, "unit": "văn bản", "kind": "count",
        "quote": (item or {}).get("text", document.get("trich_yeu") or "")[:400], "status": "khong_ro",
        "file_name": ((item or {}).get("payload") or {}).get("file_name"), "confidence": 1.0, "issues": [],
    }


def _status(stage: str, message: str, **extra) -> dict:
    return {"type": "status", "stage": stage, "message": message, **extra}


def aggregate_events(question: str, explicit_filters: dict | None = None) -> Iterator[dict]:
    started = time.perf_counter()
    yield _status("planning", "Đang lập kế hoạch tổng hợp…")
    plan = plan_aggregation(question, explicit_filters)
    warnings: list[str] = []

    yield _status("listing", "Đang liệt kê văn bản trong phạm vi…")
    total_candidates = store.count_documents(plan.filters)
    documents = list(store.iter_documents(plan.filters, limit=config.AGG_MAX_DOCS))
    truncated = total_candidates > len(documents)
    if truncated:
        warnings.append(f"Phạm vi có {total_candidates} văn bản, chỉ xét {len(documents)} văn bản đầu (AGG_MAX_DOCS). "
                        "Hãy thu hẹp thời gian hoặc loại văn bản.")
    base = {"question": question, "plan": plan.model_dump(), "scope": describe_scope(plan.model_dump()),
            "operation": plan.operation, "metric": plan.metric,
            "n_docs_in_scope": total_candidates, "n_docs_scanned": len(documents), "truncated": truncated}

    if plan.operation == "count_documents" and not plan.keywords.strip():
        rows = [_document_row(document, None) for document in documents]
        yield {"type": "aggregation_result", "data": {**base, "total": float(total_candidates), "unit": "văn bản",
               "groups": [], "included": rows, "review": [], "n_docs_with_facts": total_candidates,
               "warnings": warnings, "latency_ms": int((time.perf_counter() - started) * 1000)}}
        return

    yield _status("retrieving", f"Đang tìm đoạn chứa số liệu trong {len(documents)} văn bản…")
    relevant = _relevant_documents(plan, documents)
    by_id = {int(document["id"]): document for document in documents}

    if plan.operation == "count_documents":
        rows = [_document_row(by_id[doc_id], chunks[0]) for doc_id, chunks in relevant.items() if doc_id in by_id]
        warnings.append("Văn bản được tính khi có đoạn liên quan đạt ngưỡng; nên xem danh sách minh chứng trước khi dùng.")
        yield {"type": "aggregation_result", "data": {**base, "total": float(len(rows)), "unit": "văn bản", "groups": [],
               "included": rows, "review": [], "n_docs_with_facts": len(rows), "warnings": warnings,
               "latency_ms": int((time.perf_counter() - started) * 1000)}}
        return

    facts: list[dict] = []
    failures = 0
    targets = [(by_id[doc_id], chunks) for doc_id, chunks in relevant.items() if doc_id in by_id]
    yield _status("extracting", f"Đang trích số liệu từ {len(targets)} văn bản liên quan…", done=0, total=len(targets))
    executor = ThreadPoolExecutor(max_workers=config.AGG_CONCURRENCY, thread_name_prefix="agg")
    futures = {executor.submit(contextvars.copy_context().run, _extract_facts, plan, document, chunks): (document, chunks)
               for document, chunks in targets}
    try:
        for done, future in enumerate(as_completed(futures, timeout=remaining(config.AGG_TIMEOUT_SECONDS)), start=1):
            document, chunks = futures[future]
            try:
                facts.extend(_prepare_fact(raw, document, chunks) for raw in future.result())
            except Exception as exc:
                failures += 1
                logger.warning("Trích số liệu lỗi %s: %s", document.get("so_ky_hieu"), exc)
            yield _status("extracting", f"Đã đọc {done}/{len(targets)} văn bản…", done=done, total=len(targets))
    except (FuturesTimeout, TimeoutError):
        unfinished = sum(1 for future in futures if not future.done())
        failures += unfinished
        warnings.append(f"Hết thời gian: {unfinished} văn bản chưa được đọc.")
    finally:
        # Do not wait for in-flight LLM calls (they stop at their own timeouts); drop queued ones.
        executor.shutdown(wait=False, cancel_futures=True)
    if failures:
        warnings.append(f"{failures} văn bản không trích xuất được; kết quả có thể thiếu.")

    yield _status("computing", "Đang chuẩn hóa, chống trùng và tính toán bằng code…")
    included, review = _select_facts(plan, facts, warnings)
    groups = defaultdict(list)
    for fact in included:
        groups[_group_key(fact, plan.group_by)].append(fact)
    group_rows = [{"key": key, "value": _reduce(plan.operation, items), "n_facts": len(items),
                   "n_docs": len({fact["doc_id"] for fact in items})} for key, items in sorted(groups.items())]
    total = _reduce(plan.operation, included)
    if plan.operation == "sum" and group_rows:
        group_sum = sum(row["value"] or 0 for row in group_rows)
        if total is not None and abs(group_sum - total) > 1e-6 * max(1.0, abs(total)):
            warnings.append("Tổng theo nhóm không khớp tổng chung; kiểm tra lại dữ liệu.")
    unit = included[0]["unit"] if included and plan.operation not in {"count", "distinct_count"} else None
    yield {"type": "aggregation_result", "data": {
        **base, "total": total, "unit": unit, "groups": group_rows if plan.group_by != "none" else [],
        "included": included, "review": review[:200], "n_docs_relevant": len(targets),
        "n_docs_with_facts": len({fact["doc_id"] for fact in included}), "warnings": warnings,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }}


def aggregate(question: str, filters: dict | None = None) -> dict:
    """Non-streaming wrapper (JSON API)."""
    with budget(config.AGG_TIMEOUT_SECONDS):
        for event in aggregate_events(question, filters):
            if event["type"] == "aggregation_result":
                return event["data"]
    raise RuntimeError("Aggregation ended without a result")


_TYPE_NAMES = {"cong_van": "công văn", "ke_hoach": "kế hoạch", "bao_cao": "báo cáo", "quyet_dinh": "quyết định",
               "thong_bao": "thông báo", "to_trinh": "tờ trình", "giay_moi": "giấy mời", "nghi_quyet": "nghị quyết"}


def describe_scope(plan: dict) -> str:
    """Mô tả phạm vi lọc bằng lời để người đọc kiểm tra cách hiểu câu hỏi."""
    filters = plan.get("filters") or {}
    parts = []
    if filters.get("loai_vb"):
        parts.append("loại " + ", ".join(_TYPE_NAMES.get(value, value) for value in filters["loai_vb"]))
    if filters.get("huong"):
        parts.append(", ".join("văn bản đến" if value == "den" else "văn bản đi" for value in filters["huong"]))
    if filters.get("date_from") or filters.get("date_to"):
        parts.append(f"ngày ban hành {filters.get('date_from') or '…'} đến {filters.get('date_to') or '…'}")
    if filters.get("co_quan_ban_hanh"):
        parts.append(f"cơ quan {filters['co_quan_ban_hanh']}")
    if plan.get("keywords"):
        parts.append(f"liên quan đến \"{plan['keywords']}\"")
    return "phạm vi: " + ("; ".join(parts) if parts else "toàn bộ kho")


def summarize(data: dict) -> str:
    label = _OPERATION_LABELS.get(data.get("operation"), "Kết quả")
    total = data.get("total")
    unit = data.get("unit") or ("văn bản" if data.get("operation") == "count_documents" else None)
    return f"{label}: {format_number(total, unit)}"
