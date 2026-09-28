"""Report paired results; optionally retry missing judges without rerunning RAG."""
import argparse
from collections import Counter
import json
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._baseline_common import read_jsonl, append_jsonl, mean, percentile
from scripts.benchmark_selective import judge, latest_results, check_credit
from src.agent.schemas import AgentResult
from src.core import llm


def summarize(rows):
    judged = [r for r in rows if r.get("judge") is not None]
    answerable = [r for r in rows if r["should_answer"]]
    return {
        "cases": len(rows), "errors": sum(bool(r["result"]["error"]) for r in rows),
        "p50_seconds": percentile([r["latency_ms"] / 1000 for r in rows], .5),
        "p95_seconds": percentile([r["latency_ms"] / 1000 for r in rows], .95),
        "mean_llm_calls": mean([r["usage"]["calls"] for r in rows]),
        "mean_reported_tokens": mean([r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in rows]),
        "judged": len(judged),
        "correctness_0_2": mean([r["judge"]["correctness"] for r in judged]),
        "completeness_0_2": mean([r["judge"]["completeness"] for r in judged if isinstance(r["judge"].get("completeness"), (int, float))]),
        "grounded_claim_ratio": mean([r["judge"]["grounded_claim_ratio"] for r in judged if isinstance(r["judge"].get("grounded_claim_ratio"), (int, float))]),
        "citation_supported_rate": mean([float(r["judge"]["citation_supported"]) for r in judged if isinstance(r["judge"].get("citation_supported"), bool)]),
        "expected_document_recall": mean([r["expected_document_recall"] for r in answerable]),
        "all_expected_cited_rate": mean([float(r["all_expected_cited"]) for r in answerable]),
        "routes": dict(Counter(r["result"]["route"] for r in rows)),
    }


def question_appendix(cases, rows):
    by_pair = {(row["case_id"], row["mode"]): row for row in rows}
    categories = {"exact_lookup": "Tra cứu chính xác", "semantic_qa": "Hỏi nội dung",
                  "compare": "So sánh", "no_answer": "Không có đáp án"}
    lines = ["", "## Phụ Lục: Bộ Câu Hỏi Kiểm Thử", "",
             f"Toàn bộ {len(cases)} câu hỏi dưới đây giữ nguyên nội dung và thứ tự của bộ đo đã chốt. "
             "Đáp án kỳ vọng và trích đoạn là dữ liệu kiểm thử sinh tự động, chưa được chuyên gia xác nhận; không phải kết luận pháp lý.", "",
             "Bảng từng câu dùng kết quả pipeline cuối cùng và điểm chấm bù đã lưu. "
             "Điểm đúng là điểm AI thô (0–2), không phải đánh giá của cán bộ; dấu '-' nghĩa là chưa có điểm. "
             "Các điểm không nhất quán vẫn được giữ nguyên, không sửa để làm đẹp kết quả."]
    for index, case in enumerate(cases.values(), 1):
        case_id = case["id"]
        lines += ["", f"### {index}. {case_id}", "",
                  f"Nhóm: {categories.get(case['category'], case['category'])}.", ""]
        lines += ["> " + line for line in case["question"].splitlines()]
        refs = ", ".join(case.get("expected_so_ky_hieu", [])) or "Không có văn bản kỳ vọng trong kho"
        lines += ["", f"Văn bản đối chiếu: {refs}.", "", "Đáp án kỳ vọng:"]
        if case["should_answer"]:
            lines += ["- " + fact for fact in case.get("expected_facts", [])]
        else:
            lines += ["- Thông báo không tìm thấy hoặc không đủ bằng chứng; không suy diễn nhiệm vụ của văn bản không tồn tại."]
        for evidence in case.get("evidence", []):
            lines += ["", f"Trích đoạn đối chiếu ({evidence.get('so_ky_hieu', '')}; doc_id={evidence.get('doc_id', '')}):", ""]
            lines += ["> " + line for line in evidence.get("quote", "").splitlines()]
        lines += ["", "| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |",
                  "|---|---|---:|---:|---:|---|"]
        for mode, label in (("always", "Luôn chạy agent"), ("selective", "Chọn lọc")):
            row = by_pair[(case_id, mode)]
            score = row["judge"]["correctness"] if row.get("judge") is not None else "-"
            error = "Có" if row["result"].get("error") else "Không"
            lines.append(f"| {label} | {row['result']['route']} | {row['latency_ms'] / 1000:.3f} | "
                         f"{row['usage']['calls']} | {score} | {error} |")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--retry-missing", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Report exists; choose a new path")
    manifest = json.loads((args.run / "manifest.json").read_text())
    if not (args.run / "summary.json").exists():
        parser.error("Benchmark is paused/incomplete; do not publish a final comparison")
    if manifest["input_sha256"] != hashlib.sha256(args.input.read_bytes()).hexdigest():
        parser.error("Question set differs from the measured input")
    rows = list(latest_results(args.run / "results.jsonl").values())
    cases = {c["id"]: c for c in read_jsonl(args.input) if c.get("record_type") == "case"}
    expected_pairs = {(case_id, mode) for case_id in cases for mode in ("always", "selective")}
    if {(r["case_id"], r["mode"]) for r in rows} != expected_pairs or len(rows) != len(expected_pairs):
        parser.error("Run is incomplete or contains duplicate case/mode pairs")
    retry_file = args.run / "judge_retries.jsonl"
    retries = {(r["case_id"], r["mode"]): r for r in read_jsonl(retry_file)} if retry_file.exists() else {}
    for row in rows:
        key = row["case_id"], row["mode"]
        if row["judge"] is not None:
            continue
        if key not in retries and args.retry_missing:
            check_credit(0.10)
            try:
                with llm.track_usage() as usage:
                    assessment = judge(cases[row["case_id"]], AgentResult.model_validate(row["result"]), manifest["models"]["judge"])
                attempt = {"case_id": key[0], "mode": key[1], "judge": assessment, "usage": dict(usage)}
            except Exception as exc:
                attempt = {"case_id": key[0], "mode": key[1], "judge": None, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            append_jsonl(retry_file, attempt)
            retries[key] = attempt
            print("[judge-retry]", key, "ok" if attempt["judge"] else "unavailable", flush=True)
        if key in retries:
            row["judge"] = retries[key]["judge"]
    overall = {mode: summarize([r for r in rows if r["mode"] == mode]) for mode in ("always", "selective")}
    categories = {category: {mode: summarize([r for r in rows if r["category"] == category and r["mode"] == mode])
                             for mode in ("always", "selective")} for category in sorted({r["category"] for r in rows})}
    final = {"overall": overall, "categories": categories, "judge_retry_attempts": len(retries)}
    (args.run / "final_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Agentic RAG v3: Đo Thử Chế Độ Chọn Lọc", "",
             f"- Ngày chạy: {manifest['created_at']}",
             f"- Mẫu: {len(manifest['snapshot']['documents'])} tài liệu, {manifest['snapshot']['points']} vector; {len(cases)} câu hỏi, mỗi câu chạy ở cả hai chế độ.",
             "- Cùng snapshot, cùng câu hỏi, chạy xen kẽ thứ tự; mô hình được làm nóng trước khi đo.",
             f"- Model trả lời: `{manifest['models']['main']}`; model chấm: `{manifest['models']['judge']}`.",
             f"- Snapshot SHA-256: `{manifest['snapshot']['fingerprint']}`.",
             f"- Dữ liệu chi tiết: `{args.run.as_posix()}`. Bộ câu hỏi: `{args.input.as_posix()}`.", "",
             "## Kết Quả", "", "| Chỉ số | Luôn chạy agent | Chọn lọc |", "|---|---:|---:|"]
    labels = {"cases": "Số câu", "errors": "Lỗi pipeline", "p50_seconds": "P50 (giây)", "p95_seconds": "P95 (giây)",
              "mean_llm_calls": "Lượt gọi AI trung bình", "mean_reported_tokens": "Token pipeline trung bình (provider báo cáo)",
              "judged": "Số câu có điểm chấm", "correctness_0_2": "Độ đúng trung bình (0–2)", "completeness_0_2": "Độ đầy đủ trung bình (0–2)",
              "grounded_claim_ratio": "Tỷ lệ nhận định được hỗ trợ (LLM chấm)", "citation_supported_rate": "Tỷ lệ câu có nguồn hỗ trợ (LLM chấm)",
              "expected_document_recall": "Recall văn bản kỳ vọng", "all_expected_cited_rate": "Tỷ lệ trích đủ văn bản kỳ vọng"}
    def display(value):
        return f"{value:.3f}" if isinstance(value, float) else str(value)
    for key, label in labels.items():
        lines.append(f"| {label} | {display(overall['always'][key])} | {display(overall['selective'][key])} |")
    lines += ["", "## Theo Nhóm Câu Hỏi", "", "| Nhóm | Số câu | P50 luôn / chọn lọc (giây) | Điểm đúng luôn / chọn lọc (0–2) |", "|---|---:|---:|---:|"]
    for category, values in categories.items():
        a, s = values["always"], values["selective"]
        lines.append(f"| {category} | {a['cases']} | {display(a['p50_seconds'])} / {display(s['p50_seconds'])} | {display(a['correctness_0_2'])} / {display(s['correctness_0_2'])} |")
    lines += ["", "## Giới Hạn", "",
              "- Đây là pilot trên tài liệu ngắn được lấy mẫu từ file gốc, không phải toàn bộ kho; không đại diện cho PDF scan, bảng lớn hoặc tải đồng thời.",
              "- Câu hỏi được sinh tự động; điểm chất lượng do LLM chấm, chưa được chuyên gia xác nhận. Điểm trung bình chỉ tính các lượt có điểm chấm; số lượt đã chấm được ghi rõ ở bảng.",
              "- Thời gian là pipeline trong tiến trình, không bao gồm HTTP, Cloudflare, thời gian làm nóng mô hình hoặc thời gian chấm. Token/lượt gọi của bộ chấm được loại khỏi số liệu pipeline.",
              "- Đo ghép cặp, có thể tiếp tục qua nhiều phiên; mọi lần thử gốc được giữ trong results.jsonl và thông tin tiếp tục trong resumes.jsonl nếu có. Không phải kiểm định thống kê.",
              "- Chưa đo chính xác tổng hợp số liệu hoặc hiệu lực pháp lý. Các chức năng này vẫn cần bộ gold riêng.",
              "- Không so trực tiếp với baseline v1/v2 vì database đã reset và tập dữ liệu khác. Các baseline cũ được giữ nguyên.",
              f"- Có {len(retries)} lượt chấm lại do thiếu điểm; không chạy lại câu trả lời. Lượt đo paired-run-01 trước đó bị dừng vì lỗi provider/hạn mức và không dùng cho bảng này."]
    lines += question_appendix(cases, rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(overall, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
