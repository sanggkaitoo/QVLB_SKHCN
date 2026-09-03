from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from _baseline_common import mean, percentile, read_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tổng hợp kết quả baseline thành báo cáo Markdown.")
    parser.add_argument("--input", type=Path, default=Path("reports/generated/baseline-current.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("reports/generated/baseline-current.md"))
    return parser.parse_args()


def percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def number(value: float | None, digits: int = 2) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def metric_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return mean(values)


def judge_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values: list[float] = []
    for row in rows:
        judge = row.get("judge")
        if isinstance(judge, dict) and judge.get(key) is not None:
            try:
                values.append(float(judge[key]))
            except (TypeError, ValueError):
                pass
    return mean(values)


def category_table(rows: list[dict[str, Any]]) -> list[str]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("category") or "unknown")].append(row)

    lines = [
        "| Nhóm | Số câu | Hit@5 | MRR | Từ chối đúng | Lỗi |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for category in sorted(grouped):
        items = grouped[category]
        answerable = [row for row in items if row.get("should_answer")]
        negatives = [row for row in items if not row.get("should_answer") and row.get("abstained") is not None]
        lines.append(
            f"| {category} | {len(items)} | {percent(metric_mean(answerable, 'hit_at_5'))} "
            f"| {number(metric_mean(answerable, 'reciprocal_rank'))} "
            f"| {percent(metric_mean(negatives, 'abstained'))} "
            f"| {sum(1 for row in items if row.get('error'))} |"
        )
    return lines


def build_report(metadata: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    successful = [row for row in rows if not row.get("error")]
    answerable = [row for row in successful if row.get("should_answer")]
    negatives = [row for row in successful if not row.get("should_answer")]
    retrieval_times = [float(row["retrieval_ms"]) for row in successful if row.get("retrieval_ms") is not None]
    total_times = [float(row["answer_total_ms"]) for row in successful if row.get("answer_total_ms") is not None]
    first_token_times = [float(row["time_to_first_token_ms"]) for row in successful if row.get("time_to_first_token_ms") is not None]

    worst = sorted(
        answerable,
        key=lambda row: (row.get("expected_first_rank") is not None, -(row.get("expected_first_rank") or 9999)),
    )[:10]

    lines = [
        "# Baseline RAG hiện tại", "",
        f"- Thời điểm bắt đầu: `{metadata.get('started_at', 'N/A')}`",
        f"- Collection: `{metadata.get('collection', 'N/A')}`",
        f"- Embedding: `{metadata.get('embedding_model', 'N/A')}`",
        f"- Reranker: `{metadata.get('rerank_model', 'N/A')}`",
        f"- Model trả lời: `{metadata.get('answer_model') or 'không chạy'}`",
        f"- Model chấm: `{metadata.get('judge_model') or 'không chạy'}`",
        f"- Số câu: **{len(rows)}**, thành công: **{len(successful)}**, lỗi: **{len(rows) - len(successful)}**",
        "", "## Chỉ số chính", "",
        "| Chỉ số | Kết quả |", "|---|---:|",
        f"| Hit@1 | {percent(metric_mean(answerable, 'hit_at_1'))} |",
        f"| Hit@5 | {percent(metric_mean(answerable, 'hit_at_5'))} |",
        f"| Hit@10 | {percent(metric_mean(answerable, 'hit_at_10'))} |",
        f"| Recall@10 | {percent(metric_mean(answerable, 'recall_at_10'))} |",
        f"| MRR | {number(metric_mean(answerable, 'reciprocal_rank'))} |",
        f"| Nguồn mong đợi được trích | {percent(metric_mean(answerable, 'expected_source_cited'))} |",
        f"| Sinh câu trả lời thành công | {percent(metric_mean(successful, 'answer_success'))} |",
        f"| Từ chối đúng câu không có dữ liệu | {percent(metric_mean(negatives, 'abstained'))} |",
        f"| Correctness do judge chấm (0-2) | {number(judge_mean(successful, 'correctness'))} |",
        f"| Completeness do judge chấm (0-2) | {number(judge_mean(successful, 'completeness'))} |",
        f"| Tỷ lệ nhận định có căn cứ | {percent(judge_mean(successful, 'grounded_claim_ratio'))} |",
        "", "## Độ trễ", "",
        "| Chỉ số | Trung bình | P95 |", "|---|---:|---:|",
        f"| Retrieval | {number(mean(retrieval_times), 0)} ms | {number(percentile(retrieval_times, 0.95), 0)} ms |",
        f"| Token đầu tiên | {number(mean(first_token_times), 0)} ms | {number(percentile(first_token_times, 0.95), 0)} ms |",
        f"| Toàn bộ câu trả lời | {number(mean(total_times), 0)} ms | {number(percentile(total_times, 0.95), 0)} ms |",
        "", "## Theo nhóm", "", *category_table(successful),
        "", "## Trường hợp retrieval cần xem lại", "",
        "| ID | Nhóm | Hạng nguồn đúng | Câu hỏi |", "|---|---|---:|---|",
    ]
    for row in worst:
        question = str(row.get("question") or "").replace("|", "\\|")
        rank = row.get("expected_first_rank") or "Không tìm thấy"
        lines.append(f"| {row.get('case_id')} | {row.get('category')} | {rank} | {question} |")

    errors = [row for row in rows if row.get("error")]
    if errors:
        lines.extend(["", "## Lỗi", ""])
        for row in errors:
            lines.append(f"- `{row.get('case_id')}`: `{row.get('error')}`")

    lines.extend([
        "", "## Ghi chú", "",
        "Bộ câu hỏi này được sinh tự động từ dữ liệu hiện có. Các chỉ số retrieval dựa trên doc_id là khách quan; "
        "điểm nội dung do LLM chấm cần được kiểm tra thủ công trên một mẫu ngẫu nhiên trước khi dùng làm tiêu chí nghiệm thu.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    metadata = next((row for row in rows if row.get("record_type") == "run"), {})
    results = [row for row in rows if row.get("record_type") == "result"]
    if not results:
        raise SystemExit("Không có kết quả baseline để tổng hợp.")

    report = build_report(metadata, results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Đã tạo báo cáo tại {args.output}")
    print(json.dumps({"cases": len(results), "errors": sum(1 for row in results if row.get('error'))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
