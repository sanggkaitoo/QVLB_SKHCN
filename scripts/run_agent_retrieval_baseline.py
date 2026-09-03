from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.controller import _retrieve, _to_evidence
from src.agent.planner import create_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Đo retrieval Agentic không gọi model trả lời hoặc judge.")
    parser.add_argument("--input", type=Path, default=Path("tests/eval/generated_questions.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/generated/agent-retrieval-v2.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = [row for row in rows if row.get("record_type") == "case"]
    results = []
    started = time.perf_counter()
    for index, case in enumerate(cases, start=1):
        plan = create_plan(case["question"], explicit_filters=case.get("filters"), use_llm=False)
        items = _retrieve(case["question"], plan, plan.filters)
        evidence = _to_evidence(items)
        doc_ids = [item.metadata.get("doc_id") for item in evidence]
        expected = {int(value) for value in case.get("expected_documents", [])}
        ranks = [rank for rank, doc_id in enumerate(doc_ids, start=1) if doc_id in expected]
        first_rank = min(ranks) if ranks else None
        results.append({
            "case_id": case["id"],
            "category": case["category"],
            "should_answer": case["should_answer"],
            "expected_first_rank": first_rank,
            "retrieved_doc_ids": doc_ids,
            "correct_abstention": (not evidence) if not case["should_answer"] else None,
        })
        print(f"[agent-retrieval] {index}/{len(cases)} {case['id']} rank={first_rank}", flush=True)

    answerable = [row for row in results if row["should_answer"]]
    negatives = [row for row in results if not row["should_answer"]]
    metric = lambda predicate: sum(1 for row in answerable if predicate(row)) / len(answerable)
    summary = {
        "case_count": len(results),
        "hit_at_1": metric(lambda row: row["expected_first_rank"] == 1),
        "hit_at_5": metric(lambda row: row["expected_first_rank"] is not None and row["expected_first_rank"] <= 5),
        "hit_at_10": metric(lambda row: row["expected_first_rank"] is not None and row["expected_first_rank"] <= 10),
        "mrr": sum(1 / row["expected_first_rank"] if row["expected_first_rank"] else 0 for row in answerable) / len(answerable),
        "correct_abstention": sum(bool(row["correct_abstention"]) for row in negatives) / len(negatives) if negatives else None,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
    payload = {"summary": summary, "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
