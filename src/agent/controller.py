from __future__ import annotations

import json
import time
from typing import Any

from src.agent.planner import create_plan, rewrite_query
from src.agent.prompts import ANSWER_SYSTEM
from src.agent.schemas import AgentResult, Evidence, QueryIntent, QueryPlan
from src.agent.verifier import grade_evidence, verify_answer
from src.core import config, llm, store
from src.services import retrieval_srv
from src.core.runtime import budgeted, remaining
from src.agent.routing import simple_question, metadata_answer, strong_candidates


def _to_evidence(items: list[dict[str, Any]], limit: int = 12) -> list[Evidence]:
    evidence: list[Evidence] = []
    seen: set[tuple[Any, str]] = set()
    used_chars = 0
    for item in sorted(items, key=lambda value: float(value.get("score", 0.0)), reverse=True):
        payload = item.get("payload") or {}
        text = str(item.get("text") or "")
        key = (payload.get("doc_id"), text)
        if key in seen:
            continue
        seen.add(key)
        if not text or used_chars + len(text) + 300 > 14000:
            continue
        used_chars += len(text) + 300
        evidence.append(Evidence(
            evidence_id=f"E{len(evidence) + 1}",
            text=str(item.get("text") or ""),
            metadata=payload,
            score=float(item.get("score") or 0.0),
        ))
        if len(evidence) >= limit:
            break
    return evidence


def _retrieve(query: str, plan, filters: dict[str, Any]):
    if plan.document_refs:
        items = retrieval_srv.exact_document_search(query + " " + " ".join(plan.document_refs), top_k=8, **filters)
        if items:
            return retrieval_srv.retrieve_parent_section(retrieval_srv.retrieve_neighbors(items))
        if plan.intent == QueryIntent.EXACT_LOOKUP:
            return []
    alternatives = [value for value in plan.sub_queries if value != query]
    items = retrieval_srv.multi_query_search(
        query,
        alternative_queries=alternatives,
        top_k=8,
        loai_vb=filters.get("loai_vb"),
        huong=filters.get("huong"),
        date_from=filters.get("date_from"), date_to=filters.get("date_to"),
        co_quan_ban_hanh=filters.get("co_quan_ban_hanh"),
    )
    if items:
        seeds = items[:5]
        expanded = retrieval_srv.retrieve_neighbors(seeds, window=1)
        existing_ids = {item.get("id") for item in items}
        items.extend(item for item in expanded if item.get("id") not in existing_ids)
        items = retrieval_srv.retrieve_parent_section(items)
    return items


def _compose_answer(query: str, evidence: list[Evidence], relations: list[dict[str, Any]]) -> str:
    if not evidence:
        return "Không tìm thấy thông tin trong kho dữ liệu."
    context = "\n\n".join(
        f"[{item.evidence_id} | {item.metadata.get('so_ky_hieu', '?')} | "
        f"{item.metadata.get('section_path', 'không rõ mục')}]\n{item.text}"
        for item in evidence
    )
    relation_text = json.dumps(relations[:20], ensure_ascii=False, default=str) if relations else "[]"
    user = (
        f"CÂU HỎI: {query}\n\nBẰNG CHỨNG:\n{context}\n\n"
        f"QUAN HỆ VĂN BẢN ĐÃ BIẾT:\n{relation_text[:4000]}"
    )
    return llm.chat_with_fallback(ANSWER_SYSTEM, user, models=[config.LLM_MAIN, config.LLM_FALLBACK])


def _sources(evidence: list[Evidence]) -> list[dict[str, Any]]:
    return [
        {"text": item.text, "metadata": {**{key: value for key, value in item.metadata.items() if key not in {"text", "parent_text"}}, "evidence_id": item.evidence_id}, "score": item.score}
        for item in evidence
    ]


@budgeted
def run_agent(query: str, loai_vb: str | None = None, huong: str | None = None, routing_mode: str | None = None) -> AgentResult:
    started = time.perf_counter()
    try:
        has_data = store.has_ready_documents()
    except Exception as exc:
        return AgentResult(query=query, answer="Không kết nối được kho dữ liệu. Vui lòng kiểm tra dịch vụ database.", confidence="thap", plan=QueryPlan(), error=type(exc).__name__)
    if not has_data:
        return AgentResult(query=query, answer="Kho dữ liệu chưa có tài liệu sẵn sàng. Vui lòng crawl hoặc nhập tài liệu trước khi tra cứu.", confidence="thap", plan=QueryPlan())
    explicit_filters = {key: value for key, value in {"loai_vb": loai_vb, "huong": huong}.items() if value}
    mode = routing_mode or config.AGENT_ROUTING_MODE
    if mode not in {"always", "selective"}:
        raise ValueError("AGENT_ROUTING_MODE must be always or selective")
    plan = create_plan(query, explicit_filters=explicit_filters, use_llm=mode == "always")
    if plan.intent == QueryIntent.AGGREGATE:
        from src.agent.aggregate_tool import run_aggregate_tool
        return run_aggregate_tool(query, plan, started)
    evidence: list[Evidence] = []
    attempts = 0
    active_query = query
    error: str | None = None
    relations: list[dict[str, Any]] = []
    collected = {}
    sufficient = False
    missing = []
    fallback_reason = None

    try:
        if mode == "selective" and simple_question(query, plan):
            direct = metadata_answer(query, plan)
            if direct:
                answer, evidence = direct
                return _finish_fast(query, answer, evidence, plan, started, "metadata")
            items = _retrieve(query, plan, plan.filters)
            collected = {item.get("id") or item["text"]: item for item in items}
            evidence = _to_evidence(list(collected.values()))
            if strong_candidates(evidence):
                verified = verify_answer(query, _compose_answer(query, evidence, []), evidence)
                if verified.answer_complete and verified.claims:
                    return _finish_fast(query, verified.answer, evidence, plan, started, "verified_fast")
                fallback_reason = "verification_incomplete"
            else:
                fallback_reason = "weak_or_missing_evidence"
        if mode == "selective":
            plan = create_plan(query, explicit_filters=explicit_filters)
        for attempts in range(1, plan.max_attempts + 1):
            remaining(config.AGENT_TIMEOUT_SECONDS)
            items = list(collected.values()) if attempts == 1 and collected else _retrieve(active_query, plan, plan.filters)
            for item in items:
                collected[item.get("id") or item["text"]] = item
            evidence = _to_evidence(list(collected.values()))
            sufficient, missing = grade_evidence(query, plan, evidence)
            if sufficient:
                break
            if attempts < plan.max_attempts:
                active_query = rewrite_query(query, plan, attempts + 1, missing)

        if plan.intent == QueryIntent.LEGAL_STATUS or plan.document_refs:
            relations = retrieval_srv.find_document_relations(query)
        draft = _compose_answer(query, evidence, relations)
        verified = verify_answer(query, draft, evidence)
        answer = verified.answer
        confidence = verified.confidence
        if not sufficient:
            answer += "\n\nPhạm vi trả lời còn hạn chế do chưa tìm đủ bằng chứng cho toàn bộ câu hỏi."
            confidence = "thap"
        if relations and not any(relation.get("verified") for relation in relations):
            answer += "\n\nLưu ý: quan hệ hiệu lực tìm thấy chưa được cán bộ xác minh."
            confidence = "thap" if confidence == "trung_binh" else confidence
        answer += f"\n\nMức độ tin cậy: {confidence.replace('_', ' ')}."
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        answer = "Xử lý quá thời gian cho phép. Vui lòng thu hẹp câu hỏi và thử lại." if isinstance(exc, TimeoutError) else "Hệ thống tra cứu đang gặp lỗi xử lý. Vui lòng thử lại sau; đây không phải kết luận rằng kho dữ liệu không có thông tin."
        confidence = "thap"

    latency_ms = int((time.perf_counter() - started) * 1000)
    result = AgentResult(
        query=query,
        answer=answer,
        sources=_sources(evidence),
        confidence=confidence,
        attempts=max(attempts, 1),
        plan=plan,
        relations=relations,
        latency_ms=latency_ms,
        error=error,
        fallback_reason=fallback_reason,
    )
    if config.RAG_LOG_QUERIES:
        store.log_rag_query({
            "query_text": query,
            "intent": plan.intent.value,
            "plan": {**plan.model_dump(mode="json"), "route": result.route, "routing_mode": mode, "fallback_reason": fallback_reason},
            "source_doc_ids": sorted({int(item.metadata["doc_id"]) for item in evidence if item.metadata.get("doc_id")}),
            "rerank_scores": [item.score for item in evidence],
            "attempts": result.attempts,
            "confidence": result.confidence,
            "latency_ms": latency_ms,
            "answer_status": "ok" if not error else "error",
            "error_text": error,
        })
    return result


def _finish_fast(query, answer, evidence, plan, started, route):
    result = AgentResult(query=query, answer=answer, sources=_sources(evidence), confidence="trung_binh",
                         attempts=1, plan=plan, route=route, latency_ms=int((time.perf_counter() - started) * 1000))
    if config.RAG_LOG_QUERIES:
        store.log_rag_query({"query_text": query, "intent": plan.intent.value,
                             "plan": {**plan.model_dump(mode="json"), "route": route, "routing_mode": "selective"},
                             "source_doc_ids": sorted({item.metadata["doc_id"] for item in evidence}),
                             "rerank_scores": [item.score for item in evidence], "attempts": 1,
                             "confidence": result.confidence, "latency_ms": result.latency_ms, "answer_status": "ok"})
    return result


def answer_stream(query: str, **filters):
    result = run_agent(query, loai_vb=filters.get("loai_vb"), huong=filters.get("huong"))

    def generate():
        yield f"[SOURCES]{json.dumps(result.sources, ensure_ascii=False, default=str)}[/SOURCES]"
        for index in range(0, len(result.answer), 120):
            yield result.answer[index:index + 120]

    return generate()
