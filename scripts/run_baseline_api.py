from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._baseline_common import append_jsonl, as_int_set, normalize_text, now_iso, read_jsonl
from src.core import config, llm


NO_ANSWER_TEXT = "Không tìm thấy thông tin trong kho dữ liệu."
JUDGE_SYSTEM = """Bạn là bộ chấm độc lập cho hệ thống RAG văn bản hành chính.
Chỉ chấm dựa trên đáp án chuẩn, bằng chứng chuẩn và nguồn hệ thống đã truy xuất.
correctness và completeness là số nguyên 0, 1 hoặc 2.
grounded_claim_ratio nằm trong khoảng 0 đến 1."""
JUDGE_FORMAT = """Trả JSON:
{"correctness": 0, "completeness": 0, "grounded_claim_ratio": 0.0,
 "citation_supported": true, "reason": "giải thích ngắn"}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Đo baseline qua API RAG đang chạy.")
    parser.add_argument("--input", type=Path, default=Path("tests/eval/generated_questions.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/generated/baseline-current.jsonl"))
    parser.add_argument("--api-base", default="http://127.0.0.1:8081")
    parser.add_argument("--endpoint", default="/api/search_stream")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--judge-model", default=config.LLM_SMART)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def collect_api_answer(question: str, filters: dict[str, Any], api_base: str, endpoint: str):
    params = {"q": question}
    for key in ("loai_vb", "huong"):
        if filters.get(key):
            params[key] = filters[key]

    started = time.perf_counter()
    with requests.get(
        api_base.rstrip("/") + "/" + endpoint.lstrip("/"),
        params=params,
        stream=True,
        timeout=(10, 300),
    ) as response:
        response.raise_for_status()
        response.encoding = "utf-8"
        chunks: list[str] = []
        first_token_ms = None
        closing = "[/SOURCES]"
        stream_incomplete = False
        try:
            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                if not chunk:
                    continue
                chunks.append(chunk)
                current = "".join(chunks)
                end = current.find(closing)
                if end >= 0 and current[end + len(closing):].strip() and first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000
        except requests.exceptions.ChunkedEncodingError:
            stream_incomplete = True

    payload = "".join(chunks)
    start = payload.find("[SOURCES]")
    end = payload.find("[/SOURCES]")
    if start < 0 or end < 0:
        raise ValueError("API không trả marker SOURCES hợp lệ.")
    sources = json.loads(payload[start + len("[SOURCES]"):end])
    answer = payload[end + len("[/SOURCES]"):].strip()
    return answer, sources if isinstance(sources, list) else [], {
        "time_to_first_token_ms": first_token_ms,
        "answer_total_ms": (time.perf_counter() - started) * 1000,
        "stream_incomplete": stream_incomplete,
    }


def complete_answer(question: str, sources: list[dict[str, Any]]) -> str:
    context = ""
    for index, source in enumerate(sources[:8], start=1):
        metadata = source.get("metadata") or {}
        tag = f"{metadata.get('so_ky_hieu', '?')} - {metadata.get('ngay_ban_hanh', '?')}"
        context += f"\n[Đoạn {index} | {tag}] {source.get('text', '')}\n"
    system = """Bạn là trợ lý pháp lý của Sở Khoa học và Công nghệ.
Chỉ trả lời dựa trên các đoạn được cung cấp. Nếu không có thông tin, trả lời đúng câu:
\"Không tìm thấy thông tin trong kho dữ liệu.\"
Mỗi ý trích dẫn phải ghi nguồn [số ký hiệu - ngày]. Không suy đoán.

CÁC ĐOẠN LIÊN QUAN:
{context}"""
    return llm.chat(system.format(context=context), f"CÂU HỎI: {question}", model=config.LLM_MAIN)


def retrieval_metrics(sources: list[dict[str, Any]], expected_ids: set[int]) -> dict[str, Any]:
    retrieved_ids: list[int | None] = []
    for source in sources:
        raw = (source.get("metadata") or {}).get("doc_id")
        try:
            retrieved_ids.append(int(raw))
        except (TypeError, ValueError):
            retrieved_ids.append(None)
    ranks = [rank for rank, doc_id in enumerate(retrieved_ids, 1) if doc_id in expected_ids]
    first_rank = min(ranks) if ranks else None
    found = {doc_id for doc_id in retrieved_ids if doc_id in expected_ids}
    return {
        "retrieved_doc_ids": retrieved_ids,
        "expected_first_rank": first_rank,
        "hit_at_1": bool(first_rank and first_rank <= 1),
        "hit_at_5": bool(first_rank and first_rank <= 5),
        "hit_at_10": bool(first_rank and first_rank <= 10),
        "recall_at_10": len(found) / len(expected_ids) if expected_ids else None,
        "reciprocal_rank": 1.0 / first_rank if first_rank else 0.0,
    }


def citation_metrics(answer: str, case: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_answer = normalize_text(answer)
    expected_refs = [normalize_text(ref) for ref in case.get("expected_so_ky_hieu", []) if ref]
    returned_refs = {
        normalize_text((source.get("metadata") or {}).get("so_ky_hieu"))
        for source in sources
        if (source.get("metadata") or {}).get("so_ky_hieu")
    }
    cited = {ref for ref in returned_refs if ref in normalized_answer}
    return {
        "expected_source_cited": any(ref in normalized_answer for ref in expected_refs) if expected_refs else None,
        "returned_source_citation_ratio": len(cited) / len(returned_refs) if returned_refs else None,
        "returned_source_count": len(sources),
        "abstained": normalize_text(NO_ANSWER_TEXT) in normalized_answer,
    }


def judge(case: dict[str, Any], answer: str, sources: list[dict[str, Any]], model: str):
    source_text = "\n\n".join(
        f"[{(source.get('metadata') or {}).get('so_ky_hieu', '?')}] {str(source.get('text') or '')[:1200]}"
        for source in sources[:8]
    )
    prompt = (
        f"CÂU HỎI:\n{case.get('question', '')}\n\n"
        f"CÓ NÊN TRẢ LỜI: {bool(case.get('should_answer'))}\n\n"
        f"Ý MONG ĐỢI:\n{json.dumps(case.get('expected_facts', []), ensure_ascii=False)}\n\n"
        f"BẰNG CHỨNG CHUẨN:\n{json.dumps(case.get('evidence', []), ensure_ascii=False)}\n\n"
        f"NGUỒN TRUY XUẤT:\n{source_text[:9000]}\n\n"
        f"CÂU TRẢ LỜI:\n{answer[:6000]}\n\n{JUDGE_FORMAT}"
    )
    result = llm.extract_json(JUDGE_SYSTEM, prompt, model=model)
    return result if isinstance(result, dict) else None


def compact_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for source in sources:
        metadata = source.get("metadata") or {}
        try:
            score = float(source.get("score"))
        except (TypeError, ValueError):
            score = None
        output.append({
            "doc_id": metadata.get("doc_id"),
            "so_ky_hieu": metadata.get("so_ky_hieu"),
            "ngay_ban_hanh": metadata.get("ngay_ban_hanh"),
            "score": score,
            "text": str(source.get("text") or "")[:1600],
        })
    return output


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    cases = [row for row in rows if row.get("record_type") == "case"]
    if args.limit is not None:
        cases = cases[:args.limit]
    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"Kết quả đã tồn tại: {args.output}. Dùng --overwrite để chạy lại.")
    if args.output.exists():
        args.output.unlink()

    append_jsonl(args.output, {
        "record_type": "run",
        "started_at": now_iso(),
        "input": str(args.input),
        "case_count": len(cases),
        "collection": config.QDRANT_COLLECTION,
        "embedding_model": config.EMBED_MODEL,
        "rerank_model": config.RERANK_MODEL,
        "answer_model": config.LLM_MAIN,
        "judge_model": args.judge_model if args.judge else None,
        "api_base": args.api_base,
        "endpoint": args.endpoint,
        "top_k": 8,
        "rerank_pool": 24,
    })

    for index, case in enumerate(cases, 1):
        print(f"[baseline-api] {index}/{len(cases)} {case.get('id')}", flush=True)
        try:
            answer, sources, timing = collect_api_answer(case["question"], case.get("filters") or {}, args.api_base, args.endpoint)
            if timing.get("stream_incomplete"):
                answer = ""
                timing["answer_error"] = "LLM_MAIN stream bị ngắt trước khi trả lời hoàn chỉnh"
            expected_ids = as_int_set(case.get("expected_documents", []))
            result = {
                "record_type": "result",
                "case_id": case.get("id"),
                "category": case.get("category"),
                "question": case.get("question"),
                "should_answer": bool(case.get("should_answer")),
                "expected_documents": sorted(expected_ids),
                "retrieval_ms": None,
                **retrieval_metrics(sources, expected_ids),
                "answer": answer,
                "answer_success": bool(answer),
                "sources": compact_sources(sources),
                **timing,
                **citation_metrics(answer, case, sources),
            }
            if args.judge and answer:
                try:
                    result["judge"] = judge(case, answer, sources, args.judge_model)
                except Exception as exc:
                    result["judge_error"] = f"{type(exc).__name__}: model chấm không trả kết quả hợp lệ"
        except Exception as exc:
            result = {
                "record_type": "result",
                "case_id": case.get("id"),
                "category": case.get("category"),
                "question": case.get("question"),
                "should_answer": bool(case.get("should_answer")),
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"[baseline-api] Lỗi: {result['error']}", file=sys.stderr, flush=True)
        append_jsonl(args.output, result)

    print(f"Đã lưu kết quả tại {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
