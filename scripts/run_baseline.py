from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._baseline_common import append_jsonl, as_int_set, normalize_text, now_iso, read_jsonl
from src.core import config, llm
from src.services import search_srv


NO_ANSWER_TEXT = "Không tìm thấy thông tin trong kho dữ liệu."
SOURCES_RE = re.compile(r"^\[SOURCES\](.*?)\[/SOURCES\]$", re.DOTALL)

JUDGE_SYSTEM = """Bạn là bộ chấm độc lập cho hệ thống RAG văn bản hành chính.
Chỉ chấm dựa trên đáp án chuẩn, bằng chứng chuẩn và nguồn mà hệ thống đã truy xuất.
Không thưởng điểm cho thông tin đúng theo kiến thức bên ngoài nhưng không có trong bằng chứng.
correctness và completeness là số nguyên 0, 1 hoặc 2.
grounded_claim_ratio nằm trong khoảng 0 đến 1."""

JUDGE_FORMAT = """Trả JSON:
{
  "correctness": 0,
  "completeness": 0,
  "grounded_claim_ratio": 0.0,
  "citation_supported": true,
  "reason": "giải thích ngắn"
}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chạy bộ câu hỏi qua pipeline RAG hiện tại.")
    parser.add_argument("--input", type=Path, default=Path("tests/eval/generated_questions.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/generated/baseline-current.jsonl"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--rerank-pool", type=int, default=30)
    parser.add_argument("--judge", action="store_true", help="Dùng LLM_SMART để chấm nội dung câu trả lời.")
    parser.add_argument("--judge-model", default=config.LLM_SMART)
    parser.add_argument("--skip-answer", action="store_true", help="Chỉ đo retrieval, không gọi LLM trả lời.")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def parse_sources(chunk: str) -> list[dict[str, Any]] | None:
    match = SOURCES_RE.match(chunk.strip())
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def collect_answer(question: str, filters: dict[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, float | None]]:
    started = time.perf_counter()
    stream = search_srv.answer_stream(
        question,
        loai_vb=filters.get("loai_vb"),
        huong=filters.get("huong"),
    )
    answer_parts: list[str] = []
    sources: list[dict[str, Any]] = []
    first_token_ms: float | None = None

    for chunk in stream:
        parsed_sources = parse_sources(chunk)
        if parsed_sources is not None:
            sources = parsed_sources
            continue
        if chunk and first_token_ms is None:
            first_token_ms = (time.perf_counter() - started) * 1000
        answer_parts.append(chunk)

    total_ms = (time.perf_counter() - started) * 1000
    return "".join(answer_parts).strip(), sources, {
        "time_to_first_token_ms": first_token_ms,
        "answer_total_ms": total_ms,
    }


def rank_metrics(retrieved: list[dict[str, Any]], expected_ids: set[int]) -> dict[str, Any]:
    ranks: list[int] = []
    retrieved_ids: list[int | None] = []
    for rank, item in enumerate(retrieved, start=1):
        raw_doc_id = item.get("payload", {}).get("doc_id")
        try:
            doc_id = int(raw_doc_id)
        except (TypeError, ValueError):
            doc_id = None
        retrieved_ids.append(doc_id)
        if doc_id in expected_ids:
            ranks.append(rank)

    first_rank = min(ranks) if ranks else None
    found = {doc_id for doc_id in retrieved_ids[:10] if doc_id in expected_ids}
    return {
        "retrieved_doc_ids": retrieved_ids,
        "expected_first_rank": first_rank,
        "hit_at_1": bool(first_rank and first_rank <= 1),
        "hit_at_5": bool(first_rank and first_rank <= 5),
        "hit_at_10": bool(first_rank and first_rank <= 10),
        "recall_at_10": (len(found) / len(expected_ids)) if expected_ids else None,
        "reciprocal_rank": (1.0 / first_rank) if first_rank else 0.0,
    }


def citation_metrics(answer: str, case: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    answer_normalized = normalize_text(answer)
    expected_refs = [normalize_text(value) for value in case.get("expected_so_ky_hieu", []) if value]
    source_refs: list[str] = []
    for source in sources:
        metadata = source.get("metadata") or {}
        ref = normalize_text(metadata.get("so_ky_hieu"))
        if ref and ref != "?":
            source_refs.append(ref)

    cited_source_refs = {ref for ref in source_refs if ref in answer_normalized}
    return {
        "expected_source_cited": any(ref in answer_normalized for ref in expected_refs) if expected_refs else None,
        "returned_source_citation_ratio": (
            len(cited_source_refs) / len(set(source_refs)) if source_refs else None
        ),
        "returned_source_count": len(sources),
        "abstained": normalize_text(NO_ANSWER_TEXT) in answer_normalized,
    }


def judge_answer(case: dict[str, Any], answer: str, sources: list[dict[str, Any]], model: str) -> dict[str, Any] | None:
    evidence = case.get("evidence", [])
    source_text = "\n\n".join(
        f"[{(source.get('metadata') or {}).get('so_ky_hieu', '?')}] {source.get('text', '')[:1200]}"
        for source in sources[:8]
    )
    prompt = (
        f"CÂU HỎI:\n{case.get('question', '')}\n\n"
        f"CÓ NÊN TRẢ LỜI: {bool(case.get('should_answer'))}\n\n"
        f"Ý CHÍNH MONG ĐỢI:\n{json.dumps(case.get('expected_facts', []), ensure_ascii=False)}\n\n"
        f"BẰNG CHỨNG CHUẨN:\n{json.dumps(evidence, ensure_ascii=False, default=str)}\n\n"
        f"NGUỒN HỆ THỐNG ĐÃ TRUY XUẤT:\n{source_text[:9000]}\n\n"
        f"CÂU TRẢ LỜI HỆ THỐNG:\n{answer[:6000]}\n\n{JUDGE_FORMAT}"
    )
    judged = llm.extract_json(JUDGE_SYSTEM, prompt, model=model)
    return judged if isinstance(judged, dict) else None


def public_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in sources:
        metadata = source.get("metadata") or {}
        score = source.get("score")
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = None
        result.append({
            "doc_id": metadata.get("doc_id"),
            "so_ky_hieu": metadata.get("so_ky_hieu"),
            "ngay_ban_hanh": metadata.get("ngay_ban_hanh"),
            "score": score,
            "text": str(source.get("text") or "")[:1600],
        })
    return result


def evaluate_case(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    question = str(case.get("question") or "")
    filters = case.get("filters") or {}
    expected_ids = as_int_set(case.get("expected_documents", []))
    started = time.perf_counter()
    retrieved = search_srv.retrieve(
        question,
        top_k=args.top_k,
        rerank_pool=args.rerank_pool,
        loai_vb=filters.get("loai_vb"),
        huong=filters.get("huong"),
    )
    retrieval_ms = (time.perf_counter() - started) * 1000

    result: dict[str, Any] = {
        "record_type": "result",
        "case_id": case.get("id"),
        "category": case.get("category"),
        "question": question,
        "should_answer": bool(case.get("should_answer")),
        "expected_documents": sorted(expected_ids),
        "retrieval_ms": retrieval_ms,
        **rank_metrics(retrieved, expected_ids),
        "retrieved": [
            {
                "doc_id": item.get("payload", {}).get("doc_id"),
                "so_ky_hieu": item.get("payload", {}).get("so_ky_hieu"),
                "score": float(item.get("score", 0.0)),
            }
            for item in retrieved
        ],
    }

    if args.skip_answer:
        return result

    answer, sources, timing = collect_answer(question, filters)
    result.update({
        "answer": answer,
        "sources": public_sources(sources),
        **timing,
        **citation_metrics(answer, case, sources),
    })
    if args.judge:
        result["judge"] = judge_answer(case, answer, sources, args.judge_model)
    return result


def main() -> int:
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(f"Không tìm thấy bộ câu hỏi: {args.input}")
    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"Kết quả đã tồn tại: {args.output}. Dùng --overwrite để chạy lại.")

    rows = read_jsonl(args.input)
    cases = [row for row in rows if row.get("record_type") == "case"]
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("Bộ câu hỏi không có case hợp lệ.")

    if args.output.exists():
        args.output.unlink()
    metadata = {
        "record_type": "run",
        "started_at": now_iso(),
        "input": str(args.input),
        "case_count": len(cases),
        "collection": config.QDRANT_COLLECTION,
        "embedding_model": config.EMBED_MODEL,
        "rerank_model": config.RERANK_MODEL,
        "answer_model": None if args.skip_answer else config.LLM_MAIN,
        "judge_model": args.judge_model if args.judge else None,
        "top_k": args.top_k,
        "rerank_pool": args.rerank_pool,
    }
    append_jsonl(args.output, metadata)

    for index, case in enumerate(cases, start=1):
        print(f"[baseline] {index}/{len(cases)} {case.get('id')}: {case.get('question')}")
        try:
            result = evaluate_case(case, args)
        except Exception as exc:
            result = {
                "record_type": "result",
                "case_id": case.get("id"),
                "category": case.get("category"),
                "question": case.get("question"),
                "should_answer": bool(case.get("should_answer")),
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"[baseline] Lỗi: {result['error']}", file=sys.stderr)
        append_jsonl(args.output, result)

    print(f"Đã lưu kết quả tại {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
