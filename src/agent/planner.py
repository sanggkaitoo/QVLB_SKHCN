from __future__ import annotations

from src.agent.prompts import PLANNER_FORMAT, PLANNER_SYSTEM
from src.agent.query_analyzer import detect_filters, detect_intent
from src.agent.schemas import QueryPlan
from src.core import config, llm
from src.services.retrieval_srv import extract_document_refs


def create_plan(query: str, explicit_filters: dict | None = None, use_llm: bool = True) -> QueryPlan:
    base = QueryPlan(
        intent=detect_intent(query),
        sub_queries=[query],
        filters={**detect_filters(query), **(explicit_filters or {})},
        required_evidence=["Bằng chứng trực tiếp trả lời câu hỏi"],
        document_refs=extract_document_refs(query),
        max_attempts=config.AGENT_MAX_ATTEMPTS,
    )
    if base.intent.value in {"exact_lookup", "semantic_qa"} or not use_llm:
        return base
    try:
        planned = llm.extract_json(
            PLANNER_SYSTEM,
            f"CÂU HỎI: {query}\n\n{PLANNER_FORMAT}",
            model=config.LLM_CHEAP,
            timeout=config.AGENT_TIMEOUT_SECONDS,
        )
        if not isinstance(planned, dict):
            return base
        planned["filters"] = {**base.filters, **(planned.get("filters") or {})}
        planned["document_refs"] = planned.get("document_refs") or base.document_refs
        planned["sub_queries"] = planned.get("sub_queries") or base.sub_queries
        planned["max_attempts"] = min(config.AGENT_MAX_ATTEMPTS, int(planned.get("max_attempts") or 2))
        return QueryPlan.model_validate(planned)
    except Exception:
        return base


def rewrite_query(query: str, plan: QueryPlan, attempt: int) -> str:
    try:
        result = llm.extract_json(
            "Viết lại truy vấn để tìm bằng chứng còn thiếu. Không trả lời câu hỏi.",
            f"Truy vấn gốc: {query}\nÝ định: {plan.intent.value}\n"
            f"Bằng chứng cần có: {plan.required_evidence}\nTrả JSON: {{\"query\": \"...\"}}",
            model=config.LLM_CHEAP,
            timeout=config.AGENT_TIMEOUT_SECONDS,
        )
        if isinstance(result, dict) and result.get("query"):
            return str(result["query"]).strip()
    except Exception:
        pass
    return f"{query} {' '.join(plan.document_refs)}".strip() if attempt > 1 else query
