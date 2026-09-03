from __future__ import annotations

import json
import time
from typing import Any

from src.agent.planner import create_plan, rewrite_query
from src.agent.prompts import ANSWER_SYSTEM
from src.agent.schemas import AgentResult, Evidence, QueryIntent
from src.agent.verifier import evidence_is_sufficient, verify_answer
from src.core import config, llm, store
from src.services import retrieval_srv


def _to_evidence(items: list[dict[str, Any]], limit: int = 12) -> list[Evidence]:
    evidence: list[Evidence] = []
    seen: set[tuple[Any, str]] = set()
    for item in sorted(items, key=lambda value: float(value.get("score", 0.0)), reverse=True):
        payload = item.get("payload") or {}
        key = (payload.get("doc_id"), str(item.get("text") or "")[:180])
        if key in seen:
            continue
        seen.add(key)
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
    if plan.intent == QueryIntent.EXACT_LOOKUP:
        return retrieval_srv.exact_document_search(query, top_k=8)
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
        f"CÂU HỎI: {query}\n\nBẰNG CHỨNG:\n{context[:18000]}\n\n"
        f"QUAN HỆ VĂN BẢN ĐÃ BIẾT:\n{relation_text[:4000]}"
    )
    return llm.chat_with_fallback(ANSWER_SYSTEM, user, models=[config.LLM_MAIN, config.LLM_FALLBACK])


def _sources(evidence: list[Evidence]) -> list[dict[str, Any]]:
    return [
        {"text": item.text, "metadata": {**item.metadata, "evidence_id": item.evidence_id}, "score": item.score}
        for item in evidence
    ]


def run_agent(query: str, loai_vb: str | None = None, huong: str | None = None) -> AgentResult:
    started = time.perf_counter()
    explicit_filters = {key: value for key, value in {"loai_vb": loai_vb, "huong": huong}.items() if value}
    plan = create_plan(query, explicit_filters=explicit_filters)
    if plan.intent == QueryIntent.AGGREGATE:
        from src.agent.aggregate_tool import run_aggregate_tool
        return run_aggregate_tool(query, plan, started)
    evidence: list[Evidence] = []
    attempts = 0
    active_query = query
    error: str | None = None
    relations: list[dict[str, Any]] = []

    try:
        for attempts in range(1, plan.max_attempts + 1):
            if time.perf_counter() - started >= config.AGENT_TIMEOUT_SECONDS:
                break
            items = _retrieve(active_query, plan, plan.filters)
            evidence = _to_evidence(items)
            if evidence_is_sufficient(evidence, exact_lookup=plan.intent == QueryIntent.EXACT_LOOKUP):
                break
            if attempts < plan.max_attempts:
                active_query = rewrite_query(query, plan, attempts + 1)

        if plan.intent == QueryIntent.LEGAL_STATUS or plan.document_refs:
            relations = retrieval_srv.find_document_relations(query)
        draft = _compose_answer(query, evidence, relations)
        verified = verify_answer(query, draft, evidence)
        answer = verified.answer
        confidence = verified.confidence
        if relations and not any(relation.get("verified") for relation in relations):
            answer += "\n\nLưu ý: quan hệ hiệu lực tìm thấy chưa được cán bộ xác minh."
            confidence = "thap" if confidence == "trung_binh" else confidence
        answer += f"\n\nMức độ tin cậy: {confidence.replace('_', ' ')}."
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        answer = "Không tìm thấy thông tin trong kho dữ liệu."
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
    )
    if config.RAG_LOG_QUERIES:
        store.log_rag_query({
            "query_text": query,
            "intent": plan.intent.value,
            "plan": plan.model_dump(mode="json"),
            "source_doc_ids": sorted({int(item.metadata["doc_id"]) for item in evidence if item.metadata.get("doc_id")}),
            "rerank_scores": [item.score for item in evidence],
            "attempts": result.attempts,
            "confidence": result.confidence,
            "latency_ms": latency_ms,
            "answer_status": "ok" if not error else "error",
            "error_text": error,
        })
    return result


def answer_stream(query: str, **filters):
    result = run_agent(query, loai_vb=filters.get("loai_vb"), huong=filters.get("huong"))

    def generate():
        yield f"[SOURCES]{json.dumps(result.sources, ensure_ascii=False, default=str)}[/SOURCES]"
        for index in range(0, len(result.answer), 120):
            yield result.answer[index:index + 120]

    return generate()
