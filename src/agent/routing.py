"""Conservative fast routes; unsupported or multi-part questions use the agent."""
import re
from src.agent.schemas import QueryIntent, Evidence
from src.core import config, store
from src.services import retrieval_srv


def simple_question(query, plan):
    return (plan.intent in {QueryIntent.EXACT_LOOKUP, QueryIntent.SEMANTIC_QA}
            and len(query) <= 240 and len(plan.document_refs) <= 1
            and not re.search(r"\b(và|đồng thời|tất cả|toàn bộ|ngoại lệ)\b", query, re.IGNORECASE)
            and query.count("?") <= 1)


def metadata_answer(query, plan):
    if plan.intent != QueryIntent.EXACT_LOOKUP or len(plan.document_refs) != 1:
        return None
    # Only accept complete, narrow question templates, not arbitrary questions about a reference.
    question = retrieval_srv.DOCUMENT_REF_RE.sub("REF", query).strip().lower()
    choices = {
        "có nội dung chính là gì": ("trich_yeu", "Trích yếu"),
        "có trích yếu là gì": ("trich_yeu", "Trích yếu"),
        "được ban hành ngày nào": ("ngay_ban_hanh", "Ngày ban hành"),
        "do cơ quan nào ban hành": ("co_quan_ban_hanh", "Cơ quan ban hành"),
    }
    for phrase, (field, label) in choices.items():
        if not re.fullmatch(r"(?:văn bản\s+)?ref\s+" + re.escape(phrase) + r"[?.!\s]*", question):
            continue
        docs = store.search_documents_by_reference(plan.document_refs[0], limit=20)
        docs = [d for d in docs if retrieval_srv._passes_metadata_filters(d, plan.filters.get("date_from"), plan.filters.get("date_to"), plan.filters.get("co_quan_ban_hanh"))
                and not any(plan.filters.get(k) and plan.filters[k] != d.get(k) for k in ("loai_vb", "huong"))]
        if len(docs) != 1 or not docs[0].get(field):
            return None
        doc = docs[0]
        text = f"{label}: {doc[field]}"
        metadata = {key: str(doc.get(key) or "") for key in ("so_ky_hieu", "ngay_ban_hanh", "co_quan_ban_hanh", "source_url")}
        metadata.update(doc_id=int(doc["id"]), retrieval_tool="exact_metadata", chunk_kind="document_summary")
        return text + " [E1]", [Evidence(evidence_id="E1", text=text, metadata=metadata, score=1.0)]
    return None


def strong_candidates(evidence):
    return bool(evidence) and max(item.score for item in evidence) >= config.RAG_FAST_MIN_SCORE
