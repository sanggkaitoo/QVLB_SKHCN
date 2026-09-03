from __future__ import annotations

import re

from src.agent.prompts import VERIFIER_FORMAT, VERIFIER_SYSTEM
from src.agent.schemas import Evidence, VerificationResult
from src.core import config, llm


_CITATION_RE = re.compile(r"\[(E\d+)\]")


def evidence_is_sufficient(evidence: list[Evidence], exact_lookup: bool = False) -> bool:
    if not evidence:
        return False
    if exact_lookup:
        return any(item.metadata.get("retrieval_tool") == "exact_document_search" for item in evidence)
    strong = [item for item in evidence if item.score >= config.RAG_MIN_RERANK_SCORE]
    return len(strong) >= config.RAG_MIN_EVIDENCE


def verify_answer(query: str, draft: str, evidence: list[Evidence]) -> VerificationResult:
    if not draft or not evidence:
        return VerificationResult(answer="Không tìm thấy thông tin trong kho dữ liệu.", confidence="thap")
    evidence_text = "\n\n".join(f"[{item.evidence_id}] {item.text}" for item in evidence)
    try:
        result = llm.extract_json(
            VERIFIER_SYSTEM,
            f"CÂU HỎI: {query}\n\nBẰNG CHỨNG:\n{evidence_text[:14000]}\n\n"
            f"CÂU TRẢ LỜI NHÁP:\n{draft[:7000]}\n\n{VERIFIER_FORMAT}",
            model=config.LLM_CHEAP,
            timeout=config.AGENT_TIMEOUT_SECONDS,
        )
        if isinstance(result, dict):
            verified = VerificationResult.model_validate(result)
            valid_ids = {item.evidence_id for item in evidence}
            cited_ids = set(_CITATION_RE.findall(verified.answer))
            if cited_ids and cited_ids.issubset(valid_ids):
                return verified
    except Exception:
        pass

    valid_ids = {item.evidence_id for item in evidence}
    cited_ids = set(_CITATION_RE.findall(draft))
    confidence = "trung_binh" if cited_ids and cited_ids.issubset(valid_ids) else "thap"
    return VerificationResult(answer=draft, confidence=confidence)
