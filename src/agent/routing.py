"""Quyết định đường xử lý bằng luật: câu đơn giản, trả lời metadata, độ phủ bằng chứng."""
import re

from src.agent.schemas import Evidence, QueryIntent
from src.core import config, store
from src.services import retrieval_srv
from src.services.document_fields import normalize_document_ref

_MULTI_PART_RE = re.compile(r"\b(và|đồng thời|tất cả|toàn bộ|ngoại lệ|ngoài ra|bên cạnh)\b", re.IGNORECASE)
# "và" trong tên cơ quan không phải dấu hiệu câu hỏi nhiều ý.
_AGENCY_NAMES_RE = re.compile(
    r"(khoa học và công nghệ|thông tin và truyền thông|tài nguyên và môi trường|kế hoạch và đầu tư|"
    r"giáo dục và đào tạo|nông nghiệp và (phát triển nông thôn|môi trường)|thương binh và xã hội|"
    r"thể thao và du lịch|văn hóa và thể thao|nội vụ và|tài chính và|xây dựng và)", re.IGNORECASE)


def simple_question(query, plan):
    return (plan.intent in {QueryIntent.EXACT_LOOKUP, QueryIntent.SEMANTIC_QA}
            and len(query) <= 240 and len(plan.document_refs) <= 1
            and not _MULTI_PART_RE.search(_AGENCY_NAMES_RE.sub(" ", query))
            and query.count("?") <= 1)


def metadata_answer(query, plan):
    if plan.intent != QueryIntent.EXACT_LOOKUP or len(plan.document_refs) != 1:
        return None
    # Only accept complete, narrow question templates, not arbitrary questions about a reference.
    question = retrieval_srv.DOCUMENT_REF_RE.sub("REF", query).strip().lower()
    choices = {
        "có nội dung chính là gì": ("trich_yeu", "Trích yếu"),
        "có trích yếu là gì": ("trich_yeu", "Trích yếu"),
        "về việc gì": ("trich_yeu", "Trích yếu"),
        "được ban hành ngày nào": ("ngay_ban_hanh", "Ngày ban hành"),
        "ban hành ngày nào": ("ngay_ban_hanh", "Ngày ban hành"),
        "do cơ quan nào ban hành": ("co_quan_ban_hanh", "Cơ quan ban hành"),
        "do ai ký": ("nguoi_ky", "Người ký"),
    }
    for phrase, (field, label) in choices.items():
        if not re.fullmatch(r"(?:văn bản\s+)?(?:số\s+)?ref\s+" + re.escape(phrase) + r"[?.!\s]*", question):
            continue
        docs = [doc for doc in store.search_documents_by_reference(plan.document_refs[0], limit=20)
                if retrieval_srv._passes_metadata_filters(doc, plan.filters)]
        if len(docs) != 1 or not docs[0].get(field):
            return None
        doc = docs[0]
        text = f"{label} của văn bản {doc.get('so_ky_hieu')}: {doc[field]}"
        metadata = {key: str(doc.get(key) or "") for key in ("so_ky_hieu", "ngay_ban_hanh", "co_quan_ban_hanh", "source_url")}
        metadata.update(doc_id=int(doc["id"]), retrieval_tool="exact_metadata", chunk_kind="document_summary")
        return text + " [E1]", [Evidence(evidence_id="E1", text=text, metadata=metadata, score=1.0)]
    return None


def strong_candidates(evidence):
    return bool(evidence) and max(item.score for item in evidence) >= config.RAG_FAST_MIN_SCORE


def coverage_gaps(plan, evidence: list[Evidence], n_aspects: int = 1) -> list[str]:
    """Deterministic sufficiency check. Empty list means the LLM grader can be skipped.

    Conservative: legal-status questions and weak or partial coverage always go to the grader.
    """
    if not evidence:
        return ["Chưa tìm được bằng chứng"]
    gaps = []
    if plan.intent == QueryIntent.LEGAL_STATUS:
        gaps.append("Câu hỏi hiệu lực pháp lý cần kiểm tra bằng chứng đầy đủ")
    if not strong_candidates(evidence):
        gaps.append("Bằng chứng mạnh nhất chưa đạt ngưỡng tin cậy")
    found_refs = {normalize_document_ref(item.metadata.get("so_ky_hieu")) for item in evidence}
    for ref in plan.document_refs:
        if ref not in found_refs:
            gaps.append(f"Chưa tìm thấy văn bản {ref}")
    if n_aspects > 1:
        for aspect in range(n_aspects):
            best = max((float((item.metadata.get("aspect_scores") or {}).get(str(aspect), 0.0)) for item in evidence), default=0.0)
            if best < config.RAG_ASPECT_MIN_SCORE:
                query = plan.sub_queries[aspect] if aspect < len(plan.sub_queries) else f"khía cạnh {aspect + 1}"
                gaps.append(f"Thiếu bằng chứng cho: {query}")
    return gaps
