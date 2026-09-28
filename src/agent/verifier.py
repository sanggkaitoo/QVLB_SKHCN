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
    strong = { (item.metadata.get("doc_id"), item.metadata.get("parent_chunk_id") or item.text)
               for item in evidence if item.score >= config.RAG_MIN_RERANK_SCORE }
    return len(strong) >= config.RAG_MIN_EVIDENCE


def grade_evidence(query, plan, evidence):
    if not evidence:
        return False, ["Chưa tìm được bằng chứng"]
    try:
        result = llm.extract_json(
            "Đánh giá bằng chứng để trả lời câu hỏi. Không sử dụng kiến thức ngoài. Chỉ sufficient=true khi mọi ý bắt buộc có bằng chứng trực tiếp; không đếm số đoạn thay cho độ đầy đủ. Mâu thuẫn chưa giải quyết phải ghi vào missing.",
            f"Câu hỏi: {query}\nCác ý cần có: {plan.required_evidence}\n" +
            "\n\n".join(f"[{e.evidence_id}] {e.text}" for e in evidence) +
            '\nTrả JSON: {"sufficient": false, "missing": ["ý còn thiếu"]}',
            model=config.LLM_CHEAP, timeout=config.AGENT_TIMEOUT_SECONDS,
        )
        if isinstance(result, dict) and isinstance(result.get("sufficient"), bool) and isinstance(result.get("missing"), list):
            return result["sufficient"], [str(value) for value in result["missing"]]
    except Exception:
        pass
    return False, ["Chưa kiểm chứng được độ đầy đủ của bằng chứng"]


def verify_answer(query: str, draft: str, evidence: list[Evidence]) -> VerificationResult:
    if not draft or not evidence:
        return VerificationResult(answer="Không tìm thấy thông tin trong kho dữ liệu.", confidence="thap")
    evidence_text = "\n\n".join(f"[{item.evidence_id}] {item.text}" for item in evidence)
    try:
        result = llm.extract_json(
            VERIFIER_SYSTEM,
            f"CÂU HỎI: {query}\n\nBẰNG CHỨNG:\n{evidence_text}\n\n"
            f"CÂU TRẢ LỜI NHÁP:\n{draft}\n\n{VERIFIER_FORMAT}",
            model=config.LLM_CHEAP,
            timeout=config.AGENT_TIMEOUT_SECONDS,
        )
        if isinstance(result, dict):
            verified = VerificationResult.model_validate(result)
            valid_ids = {item.evidence_id for item in evidence}
            supported = [claim for claim in verified.claims if claim.status == "supported"
                         and claim.evidence_ids and set(claim.evidence_ids).issubset(valid_ids)]
            if supported:
                answer = "\n\n".join(_CITATION_RE.sub("", claim.claim).strip() + " " +
                                      "".join(f"[{source}]" for source in dict.fromkeys(claim.evidence_ids))
                                      for claim in supported)
                if len(supported) != len(verified.claims):
                    answer += "\n\nMột số nội dung chưa đủ bằng chứng hoặc còn mâu thuẫn nên chưa được kết luận."
                return VerificationResult(answer=answer, confidence="trung_binh", claims=supported,
                                          answer_complete=verified.answer_complete and len(supported) == len(verified.claims))
    except Exception:
        pass

    return VerificationResult(answer="Chưa đủ bằng chứng đã kiểm chứng để kết luận. Vui lòng thu hẹp câu hỏi hoặc kiểm tra tài liệu nguồn.", confidence="thap")
