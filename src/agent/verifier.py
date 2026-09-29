"""Kiểm chứng câu trả lời theo từng đoạn.

Bản nháp được tách thành các đoạn S1..Sn (giữ nguyên dòng Markdown); LLM chỉ trả trạng thái của
từng đoạn nên đầu ra rất ngắn. Câu trả lời cuối giữ nguyên văn phong và định dạng của bản nháp,
chỉ bỏ các đoạn không có bằng chứng.
"""
from __future__ import annotations

import logging
import re

from src.agent.prompts import VERIFIER_FORMAT, VERIFIER_SYSTEM
from src.agent.schemas import ClaimAssessment, Evidence, VerificationResult
from src.core import config, llm

logger = logging.getLogger(__name__)

NO_ANSWER = "Không tìm thấy thông tin trong kho dữ liệu."
UNVERIFIED_ANSWER = "Chưa đủ bằng chứng đã kiểm chứng để kết luận. Vui lòng thu hẹp câu hỏi hoặc kiểm tra tài liệu nguồn."
_CITATION_RE = re.compile(r"\[(E\d+)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+(?=[\"“(A-ZÀ-ỸĐ0-9*])")
_KEEP_STATUSES = {"supported", "partially_supported", "not_a_claim"}


def evidence_is_sufficient(evidence: list[Evidence], exact_lookup: bool = False) -> bool:
    if not evidence:
        return False
    if exact_lookup:
        return any(item.metadata.get("retrieval_tool") == "exact_document_search" for item in evidence)
    strong = {(item.metadata.get("doc_id"), item.metadata.get("parent_chunk_id") or item.text)
              for item in evidence if item.score >= config.RAG_MIN_RERANK_SCORE}
    return len(strong) >= config.RAG_MIN_EVIDENCE


def split_segments(draft: str) -> list[tuple[int, str]]:
    """(line_number, segment) pairs; list/heading prefixes stay attached to their first sentence."""
    segments = []
    for line_number, line in enumerate(draft.splitlines()):
        if not line.strip():
            continue
        for part in _SENTENCE_SPLIT_RE.split(line.strip()) if not line.lstrip().startswith("|") else [line.strip()]:
            if part.strip():
                segments.append((line_number, part.strip()))
    return segments


def _rebuild(segments: list[tuple[int, str]], keep: list[bool], original: str) -> str:
    lines: dict[int, list[str]] = {}
    for (line_number, text), kept in zip(segments, keep):
        if kept:
            lines.setdefault(line_number, []).append(text)
    original_lines = original.splitlines()
    output, previous = [], None
    for line_number in sorted(lines):
        if previous is not None and line_number - previous > 1:
            output.append("")
        indent = re.match(r"\s*", original_lines[line_number]).group(0)
        output.append(indent + " ".join(lines[line_number]))
        previous = line_number
    return "\n".join(output).strip()


def _block_ids(draft: str) -> dict[int, int]:
    """Line number -> block id. Blank lines separate blocks, except after an intro line ending with ':'
    (a list introduced by a sentence shares that sentence's block)."""
    blocks, block, previous = {}, 0, ""
    for line_number, line in enumerate(draft.splitlines()):
        if not line.strip():
            if previous and not previous.rstrip("*_ ").endswith(":"):
                block += 1
                previous = ""
            continue
        blocks[line_number] = block
        previous = line.strip()
    return blocks


def _has_content(text: str) -> bool:
    return bool(re.search(r"\w{3,}", _CITATION_RE.sub("", text)))


def verify_answer(query: str, draft: str, evidence: list[Evidence]) -> VerificationResult:
    if not draft or not evidence or draft.strip().rstrip(".") == NO_ANSWER.rstrip("."):
        return VerificationResult(answer=NO_ANSWER, confidence="thap", verified=True,
                                  changed=bool(draft) and draft.strip() != NO_ANSWER)
    valid_ids = {item.evidence_id for item in evidence}
    segments = split_segments(draft)
    if not segments:
        return VerificationResult(answer=UNVERIFIED_ANSWER, confidence="thap", changed=True)
    evidence_text = "\n\n".join(f"[{item.evidence_id}] {item.text}" for item in evidence)
    numbered = "\n".join(f"S{index}: {text}" for index, (_, text) in enumerate(segments, start=1))
    try:
        result = llm.extract_json(
            VERIFIER_SYSTEM,
            f"CÂU HỎI: {query}\n\nBẰNG CHỨNG:\n{evidence_text}\n\nCÁC ĐOẠN CẦN KIỂM CHỨNG:\n{numbered}\n\n{VERIFIER_FORMAT}",
            model=config.LLM_CHEAP,
            timeout=config.LLM_FAST_TIMEOUT_SECONDS,
            max_tokens=40 + 20 * len(segments),
        )
    except Exception as exc:
        logger.warning("Verifier LLM lỗi: %s", exc)
        result = None
    if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
        # Never release an unverified draft.
        return VerificationResult(answer=UNVERIFIED_ANSWER, confidence="thap", changed=True)

    statuses = {str(item.get("id")): str(item.get("status")) for item in result["segments"] if isinstance(item, dict)}
    blocks = _block_ids(draft)
    block_citations: dict[int, set[str]] = {}
    for line_number, text in segments:
        block_citations.setdefault(blocks[line_number], set()).update(_CITATION_RE.findall(text))
    # A block without citations borrows from the block right after it (e.g. a list followed by
    # "Thông tin này nêu tại Báo cáo … [E1]"); the LLM verdict remains the primary check.
    for block in sorted(block_citations):
        if not block_citations[block]:
            block_citations[block] = set(block_citations.get(block + 1, set()))
    keep, claims = [], []
    for index, (line_number, text) in enumerate(segments, start=1):
        status = statuses.get(f"S{index}", "unsupported")
        cited = _CITATION_RE.findall(text)
        if cited and not set(cited) <= valid_ids:
            status = "unsupported"
        if not cited and _has_content(text):
            # A list item may rely on the citation given once for its block (paragraph or introduced list).
            cited = sorted(block_citations.get(blocks[line_number], set()))
            if status in {"supported", "partially_supported"} and (not cited or not set(cited) <= valid_ids):
                status = "unsupported"  # factual content must be backed by a valid citation
        keep.append(status in _KEEP_STATUSES)
        if status != "not_a_claim":
            claims.append(ClaimAssessment(claim=text, status=status, evidence_ids=list(dict.fromkeys(cited))))

    supported = [claim for claim in claims if claim.status == "supported"]
    if not supported:
        return VerificationResult(answer=UNVERIFIED_ANSWER, confidence="thap", claims=claims, changed=True, verified=True)
    answer = _rebuild(segments, keep, draft)
    dropped = sum(1 for kept, (_, text) in zip(keep, segments) if not kept)
    partial = any(claim.status == "partially_supported" for claim in claims)
    if dropped:
        answer += "\n\nMột số nội dung chưa đủ bằng chứng hoặc còn mâu thuẫn nên đã được lược bỏ."
    complete = bool(result.get("answer_complete")) and not dropped and not partial
    confidence = "cao" if complete and len(supported) == len(claims) else ("trung_binh" if len(supported) * 2 >= len(claims) else "thap")
    return VerificationResult(answer=answer, confidence=confidence, claims=claims, answer_complete=complete,
                              changed=bool(dropped), verified=True)
