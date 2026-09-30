"""Agentic RAG: lập kế hoạch một lần -> truy xuất nhiều khía cạnh -> tự đánh giá / truy vấn bổ sung
-> trả lời (stream) -> kiểm chứng từng đoạn.

Toàn bộ pipeline là một generator sinh sự kiện; HTTP stream và run_agent() dùng chung.
Sự kiện: status, sources, token, answer (bản đã kiểm chứng thay bản nháp), done (AgentResult), error.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from src.agent.planner import create_plan, grade_evidence, rule_plan
from src.agent.prompts import ANSWER_SYSTEM
from src.agent.routing import coverage_gaps, metadata_answer, simple_question
from src.agent.schemas import AgentResult, Evidence, EvidenceGrade, QueryIntent, QueryPlan
from src.agent.verifier import NO_ANSWER, verify_answer
from src.core import config, llm, store
from src.core.runtime import budget, remaining
from src.services import retrieval_srv
from src.services.document_fields import FILE_ROLE_LABELS, normalize_filters

logger = logging.getLogger(__name__)

_EVIDENCE_CHAR_BUDGET = 14000
_HIDDEN_METADATA = {"text", "parent_text", "aspect_scores", "ingest_run", "ready", "index_version",
                    "agency_normalized", "issued_day"}


class _Timer:
    def __init__(self):
        self.stages: dict[str, int] = {}

    @contextmanager
    def __call__(self, stage: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stages[stage] = self.stages.get(stage, 0) + int((time.perf_counter() - started) * 1000)


def _status(stage: str, message: str) -> dict:
    return {"type": "status", "stage": stage, "message": message}


def _item_key(item: dict[str, Any]) -> str:
    return str(item.get("id") or item["text"])


def _to_evidence(items, limit: int = 12) -> list[Evidence]:
    evidence: list[Evidence] = []
    seen: set[tuple[Any, str]] = set()
    used_chars = 0
    for item in sorted(items, key=lambda value: float(value.get("score", 0.0)), reverse=True):
        payload = item.get("payload") or {}
        text = str(item.get("text") or "")
        key = (payload.get("doc_id"), " ".join(text.split())[:500])
        if not text or key in seen:
            continue
        if used_chars + len(text) + 300 > _EVIDENCE_CHAR_BUDGET:
            continue
        seen.add(key)
        used_chars += len(text) + 300
        evidence.append(Evidence(
            evidence_id=f"E{len(evidence) + 1}",
            text=text,
            metadata=payload,
            score=float(item.get("score") or 0.0),
        ))
        if len(evidence) >= limit:
            break
    return evidence


def _retrieve(query: str, plan: QueryPlan, aspects: list[str]) -> list[dict[str, Any]]:
    if plan.document_refs:
        items = retrieval_srv.exact_document_search(query, plan.document_refs, plan.filters, top_k=8)
        if items:
            if aspects and plan.intent != QueryIntent.EXACT_LOOKUP:
                doc_ids = sorted({item["payload"]["doc_id"] for item in items})
                items += retrieval_srv.multi_aspect_search(query, aspects, plan.filters, top_k=6, doc_ids=doc_ids)
            return retrieval_srv.retrieve_parent_section(
                sorted(retrieval_srv.retrieve_neighbors(items), key=lambda item: item["score"], reverse=True))
        if plan.intent == QueryIntent.EXACT_LOOKUP:
            return []
    top_k = 10 if plan.intent in {QueryIntent.COMPARE, QueryIntent.LEGAL_STATUS} else 8
    items = retrieval_srv.multi_aspect_search(query, aspects, plan.filters, top_k=top_k)
    if items:
        expanded = retrieval_srv.retrieve_neighbors(items[:5], window=1)
        existing = {item["id"] for item in items}
        items.extend(item for item in expanded if item["id"] not in existing)
        items = retrieval_srv.retrieve_parent_section(sorted(items, key=lambda item: item["score"], reverse=True))
    return items


def _evidence_label(item: Evidence) -> str:
    metadata = item.metadata
    parts = [item.evidence_id, metadata.get("so_ky_hieu") or "?"]
    if metadata.get("ngay_ban_hanh"):
        parts.append(str(metadata["ngay_ban_hanh"])[:10])
    if metadata.get("file_role") in {"dinh_kem", "ban_sao"}:
        parts.append(f"{FILE_ROLE_LABELS[metadata['file_role']]}: {metadata.get('file_name')}")
    parts.append(metadata.get("section_path") or ("thông tin văn bản" if metadata.get("chunk_kind") == "document_summary" else "không rõ mục"))
    return " | ".join(str(part) for part in parts)


def _answer_prompt(query: str, evidence: list[Evidence], relations: list[dict[str, Any]]) -> str:
    context = "\n\n".join(f"[{_evidence_label(item)}]\n{item.text}" for item in evidence)
    relation_text = json.dumps([
        {key: relation.get(key) for key in ("source_so_ky_hieu", "relation_type", "target_ref_text",
                                            "target_so_ky_hieu", "verified", "evidence_text")}
        for relation in relations[:20]
    ], ensure_ascii=False, default=str) if relations else "[]"
    return (f"CÂU HỎI: {query}\n\nBẰNG CHỨNG:\n{context}\n\n"
            f"QUAN HỆ VĂN BẢN ĐÃ BIẾT (chưa xác minh nếu verified=false):\n{relation_text[:4000]}")


def _sources(evidence: list[Evidence]) -> list[dict[str, Any]]:
    output = []
    for item in evidence:
        metadata = {key: value for key, value in item.metadata.items() if key not in _HIDDEN_METADATA}
        if metadata.get("file_role"):
            metadata["file_role_label"] = FILE_ROLE_LABELS.get(metadata["file_role"], metadata["file_role"])
        output.append({"text": item.text, "metadata": {**metadata, "evidence_id": item.evidence_id}, "score": item.score})
    return output


def _finish(result: AgentResult, mode: str) -> Iterator[dict]:
    if config.RAG_LOG_QUERIES:
        store.log_rag_query({
            "query_text": result.query,
            "intent": result.plan.intent.value,
            "plan": {**result.plan.model_dump(mode="json"), "route": result.route, "routing_mode": mode,
                     "fallback_reason": result.fallback_reason, "timings": result.timings, "verified": result.verified},
            "source_doc_ids": sorted({int(item["metadata"]["doc_id"]) for item in result.sources if item["metadata"].get("doc_id")}),
            "rerank_scores": [float(item.get("score") or 0.0) for item in result.sources],
            "attempts": result.attempts,
            "confidence": result.confidence,
            "latency_ms": result.latency_ms,
            "answer_status": "ok" if not result.error else "error",
            "error_text": result.error,
            "user_id": llm.current_scope().get("user_id"),
            "model": llm.current_scope().get("served_model"),
        })
    yield {"type": "done", "result": result.model_dump(mode="json")}


def _answer_pipeline(query: str, base: QueryPlan, explicit: dict, mode: str, started: float,
                     timer: _Timer) -> Iterator[dict]:
    plan, route, fallback_reason = base, "agent", None
    evidence: list[Evidence] = []
    relations: list[dict[str, Any]] = []
    attempts, error, verified = 0, None, False
    grade = EvidenceGrade(sufficient=False)
    answer, confidence = "", "thap"
    try:
        if mode == "selective":
            direct = metadata_answer(query, base)
            if direct:
                answer, evidence = direct
                yield {"type": "sources", "sources": _sources(evidence)}
                yield {"type": "token", "text": answer}
                yield {"type": "answer", "answer": answer, "verified": True, "confidence": "cao", "changed": False}
                result = AgentResult(query=query, answer=answer, sources=_sources(evidence), confidence="cao",
                                     plan=base, route="metadata", verified=True, timings=timer.stages,
                                     latency_ms=int((time.perf_counter() - started) * 1000))
                yield from _finish(result, mode)
                return

        use_llm_plan = mode == "always" or not simple_question(query, base)
        if use_llm_plan:
            yield _status("planning", "Đang phân tích câu hỏi và lập kế hoạch tra cứu…")
            with timer("plan"):
                plan = create_plan(query, explicit, use_llm=True, base=base)
        aspects = plan.sub_queries[1:]
        n_aspects = min(len(plan.sub_queries), config.RAG_MULTI_QUERY_COUNT) if aspects else 1
        collected: dict[str, dict[str, Any]] = {}
        active_query = query
        for attempts in range(1, plan.max_attempts + 1):
            remaining(config.AGENT_TIMEOUT_SECONDS)
            yield _status("retrieving", "Đang tìm bằng chứng trong kho văn bản…" if attempts == 1
                          else f"Đang tìm bổ sung (vòng {attempts}): {active_query[:80]}")
            with timer("retrieve"):
                items = _retrieve(active_query, plan, aspects)
            for item in items:
                key = _item_key(item)
                if key not in collected or item["score"] > collected[key]["score"]:
                    collected[key] = item
            evidence = _to_evidence(collected.values())
            if not evidence and attempts == 1 and plan.intent == QueryIntent.EXACT_LOOKUP:
                grade = EvidenceGrade(sufficient=False, missing=["Không có văn bản khớp số ký hiệu"], graded_by="rules")
                break
            if attempts == 1 and mode == "selective":
                gaps = coverage_gaps(plan, evidence, n_aspects)
                if not gaps:
                    grade = EvidenceGrade(sufficient=True, graded_by="rules")
                    route = "agent" if use_llm_plan else "fast"
                    break
                fallback_reason = gaps[0]
            yield _status("grading", "Đang tự đánh giá độ đầy đủ của bằng chứng…")
            with timer("grade"):
                grade = grade_evidence(query, plan, evidence)
            if grade.sufficient or attempts >= plan.max_attempts:
                break
            follow_up = grade.next_queries or [f"{query} {grade.missing[0]}" if grade.missing else query]
            active_query, aspects = follow_up[0], follow_up[1:]

        if evidence and (plan.intent == QueryIntent.LEGAL_STATUS or plan.document_refs):
            with timer("relations"):
                relations = retrieval_srv.find_document_relations(
                    list(dict.fromkeys(item.metadata.get("doc_id") for item in evidence))[:6])

        yield {"type": "sources", "sources": _sources(evidence)}
        if not evidence:
            answer, confidence, verified = NO_ANSWER, "thap", True
            yield {"type": "token", "text": answer}
        else:
            yield _status("answering", "Đang soạn câu trả lời…")
            draft = ""
            with timer("answer"):
                for text in llm.chat_stream(ANSWER_SYSTEM, _answer_prompt(query, evidence, relations)):
                    draft += text
                    yield {"type": "token", "text": text}
            yield _status("verifying", "Đang kiểm chứng từng nhận định với nguồn…")
            with timer("verify"):
                verification = verify_answer(query, draft, evidence)
            answer, confidence, verified = verification.answer, verification.confidence, verification.verified
            if not grade.sufficient and answer != NO_ANSWER:
                answer += "\n\nPhạm vi trả lời còn hạn chế do chưa tìm đủ bằng chứng cho toàn bộ câu hỏi."
                confidence = "thap"
            if relations and not any(relation.get("verified") for relation in relations):
                answer += "\n\nLưu ý: quan hệ giữa các văn bản được trích tự động, chưa được cán bộ xác minh."
                confidence = "thap" if confidence != "cao" else "trung_binh"
        if answer != NO_ANSWER:
            answer += f"\n\nMức độ tin cậy: {confidence.replace('_', ' ')}."
    except Exception as exc:
        logger.exception("Agent pipeline failed")
        error = f"{type(exc).__name__}: {exc}"
        answer = ("Xử lý quá thời gian cho phép. Vui lòng thu hẹp câu hỏi và thử lại." if isinstance(exc, TimeoutError)
                  else "Hệ thống tra cứu đang gặp lỗi xử lý. Vui lòng thử lại sau; đây không phải kết luận rằng kho dữ liệu không có thông tin.")
        confidence, verified = "thap", False
    yield {"type": "answer", "answer": answer, "verified": verified, "confidence": confidence, "changed": True}
    result = AgentResult(
        query=query, answer=answer, sources=_sources(evidence), confidence=confidence,
        attempts=max(attempts, 1), plan=plan, relations=relations, error=error, route=route,
        fallback_reason=fallback_reason, verified=verified, timings=timer.stages,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    yield from _finish(result, mode)


def agent_events(query: str, loai_vb=None, huong=None, filters: dict | None = None,
                 routing_mode: str | None = None, user_id: int | None = None,
                 answer_model: str | None = None) -> Iterator[dict]:
    """answer_model: model người dùng chọn cho bước trả lời (None = theo cấu hình admin)."""
    with llm.request_scope(user_id=user_id, answer_model=answer_model):
        yield from _agent_events(query, loai_vb, huong, filters, routing_mode)


def _agent_events(query: str, loai_vb, huong, filters: dict | None, routing_mode: str | None) -> Iterator[dict]:
    started = time.perf_counter()
    mode = routing_mode or config.AGENT_ROUTING_MODE
    if mode not in {"always", "selective"}:
        raise ValueError("AGENT_ROUTING_MODE must be always or selective")
    explicit = normalize_filters({**(filters or {}), **{key: value for key, value in
                                                         {"loai_vb": loai_vb, "huong": huong}.items() if value}})
    timer = _Timer()
    try:
        has_data = store.has_ready_documents()
    except Exception as exc:
        message = "Không kết nối được kho dữ liệu. Vui lòng kiểm tra dịch vụ database."
        yield {"type": "answer", "answer": message, "verified": False, "confidence": "thap", "changed": True}
        yield from _finish(AgentResult(query=query, answer=message, plan=QueryPlan(), error=type(exc).__name__), mode)
        return
    if not has_data:
        message = "Kho dữ liệu chưa có tài liệu sẵn sàng. Vui lòng crawl hoặc nhập tài liệu trước khi tra cứu."
        yield {"type": "answer", "answer": message, "verified": False, "confidence": "thap", "changed": True}
        yield from _finish(AgentResult(query=query, answer=message, plan=QueryPlan()), mode)
        return
    yield _status("analyzing", "Đang phân tích câu hỏi…")
    with timer("analyze"):
        base = rule_plan(query, explicit)
    if base.intent == QueryIntent.AGGREGATE:
        from src.agent.aggregate_tool import aggregate_events
        with budget(config.AGG_TIMEOUT_SECONDS):
            yield from aggregate_events(query, base, started, explicit, finish=lambda result: _finish(result, mode))
        return
    with budget(config.AGENT_TIMEOUT_SECONDS):
        yield from _answer_pipeline(query, base, explicit, mode, started, timer)


def run_agent(query: str, loai_vb: str | None = None, huong: str | None = None,
              routing_mode: str | None = None, filters: dict | None = None,
              user_id: int | None = None, answer_model: str | None = None) -> AgentResult:
    """Non-streaming entry point (benchmarks, scripts): consumes the same event pipeline."""
    result = None
    for event in agent_events(query, loai_vb=loai_vb, huong=huong, filters=filters, routing_mode=routing_mode,
                              user_id=user_id, answer_model=answer_model):
        if event["type"] == "done":
            result = AgentResult.model_validate(event["result"])
    if result is None:
        raise RuntimeError("Agent pipeline ended without a result")
    return result
