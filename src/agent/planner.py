from __future__ import annotations

import logging

from src.agent.prompts import GRADER_FORMAT, GRADER_SYSTEM, PLANNER_FORMAT, PLANNER_SYSTEM
from src.agent.query_analyzer import detect_filters, detect_intent, partial_reference_numbers
from src.agent.schemas import Evidence, EvidenceGrade, QueryIntent, QueryPlan
from src.core import config, llm, store
from src.services.document_fields import extract_document_refs, normalize_document_ref, normalize_filters

logger = logging.getLogger(__name__)


def _resolve_partial_refs(query: str) -> list[str]:
    """'công văn 2072' -> số ký hiệu đầy đủ, chỉ khi số đó xác định duy nhất một văn bản."""
    refs = []
    for number in partial_reference_numbers(query):
        try:
            documents = store.search_documents_by_number(number, limit=2)
        except Exception as exc:
            logger.warning("Không tra được số văn bản %s: %s", number, exc)
            continue
        if len(documents) == 1 and documents[0].get("normalized_so_ky_hieu"):
            refs.append(documents[0]["normalized_so_ky_hieu"])
    return refs


def rule_plan(query: str, explicit_filters: dict | None = None) -> QueryPlan:
    refs = extract_document_refs(query)
    refs += [ref for ref in _resolve_partial_refs(query) if ref not in refs]
    intent = detect_intent(query)
    if intent == QueryIntent.SEMANTIC_QA and refs:
        intent = QueryIntent.EXACT_LOOKUP
    return QueryPlan(
        intent=intent,
        sub_queries=[query],
        filters=normalize_filters({**detect_filters(query), **normalize_filters(explicit_filters)}),
        required_evidence=["Bằng chứng trực tiếp trả lời câu hỏi"],
        document_refs=refs,
        max_attempts=config.AGENT_MAX_ATTEMPTS,
    )


def create_plan(query: str, explicit_filters: dict | None = None, use_llm: bool = True,
                base: QueryPlan | None = None) -> QueryPlan:
    """Lập kế hoạch MỘT lần cho mỗi câu hỏi: luật trước, LLM chỉ khi cần tách khía cạnh."""
    base = base or rule_plan(query, explicit_filters)
    if not use_llm:
        return base
    try:
        planned = llm.extract_json(
            PLANNER_SYSTEM,
            f"CÂU HỎI: {query}\n\n{PLANNER_FORMAT}",
            model=llm.PLAN,
            timeout=config.LLM_FAST_TIMEOUT_SECONDS,
            max_tokens=800,
        )
    except Exception as exc:
        logger.warning("Planner LLM lỗi, dùng kế hoạch theo luật: %s", exc)
        return base
    if not isinstance(planned, dict):
        return base
    try:
        # Filters come only from the UI and the rule analyzer: model-guessed filters (agency, type, dates)
        # were a frequent cause of empty retrieval for questions that did not ask for them.
        filters = base.filters
        refs = list(dict.fromkeys(base.document_refs + [normalize_document_ref(ref) for ref in planned.get("document_refs") or []
                                                        if extract_document_refs(str(ref))]))
        sub_queries = [" ".join(str(value).split()) for value in planned.get("sub_queries") or [] if str(value).strip()]
        intent = planned.get("intent") if planned.get("intent") in {item.value for item in QueryIntent} else base.intent.value
        # Aggregation is a different tool; only the rule analyzer may route there (and out of there).
        if base.intent in {QueryIntent.COMPARE, QueryIntent.LEGAL_STATUS, QueryIntent.AGGREGATE} or intent == "aggregate":
            intent = base.intent.value
        return QueryPlan(
            intent=intent,
            sub_queries=list(dict.fromkeys([query] + sub_queries))[:config.RAG_MULTI_QUERY_COUNT + 1],
            filters=filters,
            required_evidence=[str(value) for value in planned.get("required_evidence") or []][:6] or base.required_evidence,
            document_refs=refs,
            max_attempts=config.AGENT_MAX_ATTEMPTS,
            planned_by="llm",
        )
    except Exception as exc:
        logger.warning("Kế hoạch LLM không hợp lệ: %s", exc)
        return base


def grade_evidence(query: str, plan: QueryPlan, evidence: list[Evidence]) -> EvidenceGrade:
    """Một lượt LLM vừa đánh giá độ đủ vừa đề xuất truy vấn cho phần còn thiếu."""
    if not evidence:
        return EvidenceGrade(sufficient=False, missing=["Chưa tìm được bằng chứng"], next_queries=[], graded_by="rules")
    context = "\n\n".join(f"[{item.evidence_id} | {item.metadata.get('so_ky_hieu') or '?'}] {item.text[:1500]}"
                          for item in evidence)
    try:
        result = llm.extract_json(
            GRADER_SYSTEM,
            f"Câu hỏi: {query}\nCác ý cần có: {plan.required_evidence}\n\nBẰNG CHỨNG:\n{context}\n\n{GRADER_FORMAT}",
            model=llm.PLAN, timeout=config.LLM_FAST_TIMEOUT_SECONDS, max_tokens=600,
        )
        if isinstance(result, dict) and isinstance(result.get("sufficient"), bool):
            return EvidenceGrade(
                sufficient=result["sufficient"],
                missing=[str(value) for value in result.get("missing") or []][:6],
                next_queries=[" ".join(str(value).split()) for value in result.get("next_queries") or [] if str(value).strip()][:2],
            )
    except Exception as exc:
        logger.warning("Grader LLM lỗi: %s", exc)
    return EvidenceGrade(sufficient=False, missing=["Chưa kiểm chứng được độ đầy đủ của bằng chứng"], graded_by="error")
