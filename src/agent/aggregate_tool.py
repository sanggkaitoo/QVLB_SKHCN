from __future__ import annotations

import time

from src.agent.schemas import AgentResult, QueryPlan
from src.core import config, store
from src.services import aggregate_srv


def run_aggregate_tool(query: str, plan: QueryPlan, started: float) -> AgentResult:
    try:
        data = aggregate_srv.aggregate(query)
        rows = data.get("evidence") or []
        sources = []
        for index, row in enumerate(rows, start=1):
            sources.append({
                "text": str(row.get("evidence") or ""),
                "metadata": {
                    "evidence_id": f"E{index}",
                    "so_ky_hieu": row.get("so_ky_hieu"),
                    "ngay_ban_hanh": row.get("ngay_ban_hanh"),
                    "value": row.get("value"),
                    "unit": row.get("unit"),
                    "retrieval_tool": "aggregate_documents",
                },
                "score": 1.0,
            })
        if rows:
            answer = (
                f"Kết quả {data.get('agg', 'tổng hợp')} cho \"{data.get('metric', query)}\": "
                f"{data.get('total')} ({data.get('n_docs_matched')} văn bản có bằng chứng)."
            )
            confidence = "cao"
        else:
            answer = "Không tìm thấy thông tin trong kho dữ liệu."
            confidence = "thap"
        latency_ms = int((time.perf_counter() - started) * 1000)
        result = AgentResult(
            query=query,
            answer=answer + f"\n\nMức độ tin cậy: {confidence}.",
            sources=sources,
            confidence=confidence,
            attempts=1,
            plan=plan,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        result = AgentResult(
            query=query,
            answer="Không tìm thấy thông tin trong kho dữ liệu.\n\nMức độ tin cậy: thấp.",
            confidence="thap",
            attempts=1,
            plan=plan,
            latency_ms=latency_ms,
            error=f"{type(exc).__name__}: {exc}",
        )

    if config.RAG_LOG_QUERIES:
        store.log_rag_query({
            "query_text": query,
            "intent": plan.intent.value,
            "plan": plan.model_dump(mode="json"),
            "source_doc_ids": [],
            "rerank_scores": [1.0 for _ in result.sources],
            "attempts": 1,
            "confidence": result.confidence,
            "latency_ms": result.latency_ms,
            "answer_status": "ok" if not result.error else "error",
            "error_text": result.error,
        })
    return result
