from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._baseline_common import normalize_text, now_iso, write_jsonl
from src.core import config, llm, store


GENERATOR_SYSTEM = """Bạn tạo dữ liệu đánh giá cho hệ thống tra cứu văn bản hành chính Việt Nam.
Chỉ dùng nội dung trong VĂN BẢN. Tạo đúng một câu hỏi tự nhiên mà cán bộ có thể hỏi.
Câu hỏi không được nhắc số ký hiệu văn bản, tên file hoặc cụm 'theo đoạn trên'.
Đáp án phải ngắn, rõ và được chứng minh hoàn toàn bởi một câu trích nguyên văn.
Không suy luận thêm kiến thức ngoài văn bản."""

GENERATOR_FORMAT = """Trả JSON:
{
  "question": "câu hỏi tiếng Việt",
  "expected_answer": "đáp án ngắn",
  "evidence_quote": "một câu được chép nguyên văn từ VĂN BẢN"
}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sinh bộ câu hỏi baseline có nguồn chuẩn từ kho văn bản.")
    parser.add_argument("--count", type=int, default=30, help="Tổng số câu hỏi cần tạo.")
    parser.add_argument("--output", type=Path, default=Path("tests/eval/generated_questions.jsonl"))
    parser.add_argument("--seed", type=int, default=20260723)
    parser.add_argument("--model", default=config.LLM_SMART)
    parser.add_argument("--max-document-chars", type=int, default=7000)
    return parser.parse_args()


def load_candidates(limit: int = 600) -> list[dict[str, Any]]:
    sql = """
        SELECT id, so_ky_hieu, ngay_ban_hanh, loai_vb, huong,
               co_quan_ban_hanh, trich_yeu, full_text
        FROM documents
        WHERE so_ky_hieu IS NOT NULL
          AND btrim(so_ky_hieu) <> ''
          AND full_text IS NOT NULL
          AND length(full_text) >= 900
        ORDER BY id DESC
        LIMIT %s
    """
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute(sql, (limit,))
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def stratified_sample(rows: list[dict[str, Any]], count: int, rng: random.Random) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("loai_vb") or "khac")].append(row)
    for group in groups.values():
        rng.shuffle(group)

    selected: list[dict[str, Any]] = []
    keys = sorted(groups, key=lambda key: len(groups[key]), reverse=True)
    while len(selected) < min(count, len(rows)):
        progressed = False
        for key in keys:
            if groups[key]:
                selected.append(groups[key].pop())
                progressed = True
                if len(selected) >= count:
                    break
        if not progressed:
            break
    return selected


def compact_excerpt(text: str, limit: int) -> str:
    text = " ".join((text or "").replace("\x00", " ").split())
    return text[:limit]


def exact_case(index: int, doc: dict[str, Any]) -> dict[str, Any]:
    so_ky = str(doc["so_ky_hieu"]).strip()
    trich_yeu = str(doc.get("trich_yeu") or "").strip()
    question = f"Văn bản {so_ky} có nội dung chính là gì?"
    expected_answer = trich_yeu or compact_excerpt(str(doc.get("full_text") or ""), 320)
    return {
        "record_type": "case",
        "id": f"exact_{index:03d}",
        "category": "exact_lookup",
        "question": question,
        "expected_documents": [int(doc["id"])],
        "expected_so_ky_hieu": [so_ky],
        "expected_facts": [expected_answer],
        "evidence": [{"doc_id": int(doc["id"]), "so_ky_hieu": so_ky, "quote": expected_answer}],
        "should_answer": True,
        "filters": {},
    }


def semantic_case(index: int, doc: dict[str, Any], model: str, max_chars: int) -> dict[str, Any] | None:
    excerpt = compact_excerpt(str(doc.get("full_text") or ""), max_chars)
    prompt = f"VĂN BẢN:\n{excerpt}\n\n{GENERATOR_FORMAT}"
    generated = llm.extract_json(GENERATOR_SYSTEM, prompt, model=model)
    if not isinstance(generated, dict):
        return None

    question = str(generated.get("question") or "").strip()
    answer = str(generated.get("expected_answer") or "").strip()
    quote = str(generated.get("evidence_quote") or "").strip()
    if not question or not answer or len(quote) < 20:
        return None
    if normalize_text(quote) not in normalize_text(excerpt):
        return None

    so_ky = str(doc["so_ky_hieu"]).strip()
    return {
        "record_type": "case",
        "id": f"semantic_{index:03d}",
        "category": "semantic_qa",
        "question": question,
        "expected_documents": [int(doc["id"])],
        "expected_so_ky_hieu": [so_ky],
        "expected_facts": [answer],
        "evidence": [{"doc_id": int(doc["id"]), "so_ky_hieu": so_ky, "quote": quote}],
        "should_answer": True,
        "filters": {},
    }


def no_answer_case(index: int, seed: int) -> dict[str, Any]:
    fake_number = 900000 + ((seed + index * 7919) % 99999)
    fake_ref = f"{fake_number}/ZZ-KHONGTONTAI"
    return {
        "record_type": "case",
        "id": f"no_answer_{index:03d}",
        "category": "no_answer",
        "question": f"Văn bản {fake_ref} quy định những nhiệm vụ nào?",
        "expected_documents": [],
        "expected_so_ky_hieu": [],
        "expected_facts": [],
        "evidence": [],
        "should_answer": False,
        "filters": {},
    }


def main() -> int:
    args = parse_args()
    if args.count < 10:
        raise SystemExit("--count phải từ 10 trở lên để các nhóm câu hỏi có ý nghĩa.")

    rng = random.Random(args.seed)
    candidates = load_candidates()
    if len(candidates) < 10:
        raise SystemExit("Kho dữ liệu không có đủ văn bản hợp lệ để tạo baseline.")

    exact_target = max(1, round(args.count * 0.35))
    no_answer_target = max(1, round(args.count * 0.20))
    semantic_target = args.count - exact_target - no_answer_target
    sampled = stratified_sample(candidates, max(exact_target, semantic_target * 3), rng)

    cases: list[dict[str, Any]] = []
    for index, doc in enumerate(sampled[:exact_target], start=1):
        cases.append(exact_case(index, doc))

    semantic_count = 0
    for doc in sampled:
        if semantic_count >= semantic_target:
            break
        try:
            case = semantic_case(semantic_count + 1, doc, args.model, args.max_document_chars)
        except Exception as exc:
            print(f"[generate] Bỏ qua doc_id={doc['id']}: {exc}", file=sys.stderr)
            continue
        if case:
            cases.append(case)
            semantic_count += 1
            print(f"[generate] semantic {semantic_count}/{semantic_target}")

    for index in range(1, no_answer_target + 1):
        cases.append(no_answer_case(index, args.seed))

    if semantic_count < semantic_target:
        print(
            f"[generate] Cảnh báo: chỉ tạo được {semantic_count}/{semantic_target} câu semantic có trích dẫn nguyên văn.",
            file=sys.stderr,
        )

    metadata = {
        "record_type": "dataset",
        "created_at": now_iso(),
        "seed": args.seed,
        "generator_model": args.model,
        "requested_count": args.count,
        "actual_count": len(cases),
        "collection": config.QDRANT_COLLECTION,
    }
    rng.shuffle(cases)
    write_jsonl(args.output, [metadata, *cases])
    print(f"Đã tạo {len(cases)} câu hỏi tại {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
