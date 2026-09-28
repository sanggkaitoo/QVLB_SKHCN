"""Paired in-process benchmark: same frozen corpus/questions, no web server."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import httpx
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._baseline_common import read_jsonl, append_jsonl, mean, percentile, now_iso
from scripts.run_baseline import JUDGE_SYSTEM, JUDGE_FORMAT
from src.core import config, store, llm, embedder
from src.agent.controller import run_agent


def latest_results(path):
    rows = read_jsonl(path) if path.exists() else []
    return {(r["case_id"], r["mode"]): r for r in rows}


def check_credit(minimum):
    if urlparse(config.LLM_BASE_URL).hostname != "openrouter.ai":
        return
    response = httpx.get("https://openrouter.ai/api/v1/credits", headers={"Authorization": "Bearer " + config.LLM_API_KEY}, timeout=10)
    response.raise_for_status()
    data = response.json()["data"]
    balance = float(data["total_credits"]) - float(data["total_usage"])
    if balance < minimum:
        raise RuntimeError(f"OpenRouter balance ${balance:.4f} is below the benchmark safety reserve ${minimum:.2f}")


def corpus():
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id,sha256,index_version,n_chunks FROM documents WHERE ingest_status='ready' ORDER BY id")
        rows = cursor.fetchall()
    return {"documents": rows, "fingerprint": hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
            "collection": config.RAG_COLLECTION, "points": store._q.get_collection(config.RAG_COLLECTION).points_count}


def judge(case, result, model):
    if result.error:
        return {"correctness": 0, "completeness": 0, "grounded_claim_ratio": None, "citation_supported": False, "reason": "Pipeline error"}
    prompt = json.dumps({"question": case["question"], "should_answer": case["should_answer"],
                         "expected_facts": case.get("expected_facts"), "gold_evidence": case.get("evidence"),
                         "answer": result.answer, "sources": result.sources}, ensure_ascii=False, default=str)
    value = llm.extract_json(JUDGE_SYSTEM, prompt + "\n" + JUDGE_FORMAT, model=model, timeout=45)
    if not isinstance(value, dict) or value.get("correctness") not in (0, 1, 2):
        return None
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judge-model", default=config.LLM_SMART)
    parser.add_argument("--resume", action="store_true", help="Resume missing/failed pairs; retain all original attempts")
    parser.add_argument("--minimum-credit-usd", type=float, default=0.10)
    args = parser.parse_args()
    if args.output.exists() and not args.resume:
        parser.error("Output already exists; use a new directory")
    cases = [r for r in read_jsonl(args.input) if r.get("record_type") == "case"]
    snapshot = corpus()
    if not snapshot["documents"] or not cases:
        parser.error("Cannot benchmark an empty corpus or empty case set")
    ids = {r[0] for r in snapshot["documents"]}
    if any(not set(case.get("expected_documents", [])).issubset(ids) for case in cases):
        parser.error("Gold document IDs do not belong to the current corpus")
    args.output.mkdir(parents=True, exist_ok=args.resume)
    previous = None
    if args.resume:
        previous = json.loads((args.output / "manifest.json").read_text())
        current_code = hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "src").rglob("*.py")))).hexdigest()
        if previous["snapshot"] != json.loads(json.dumps(snapshot)) or previous["input_sha256"] != hashlib.sha256(args.input.read_bytes()).hexdigest() or previous["code_sha256"] != current_code:
            parser.error("Corpus, question set or application code changed; start a new run")
        if previous["models"] != {"main": config.LLM_MAIN, "cheap": config.LLM_CHEAP, "judge": args.judge_model}:
            parser.error("Model configuration changed; start a new run")
    try:
        check_credit(args.minimum_credit_usd)
    except Exception as exc:
        (args.output / "status.json").write_text(json.dumps({"status": "paused", "reason": str(exc)}, ensure_ascii=False), encoding="utf-8")
        raise SystemExit(str(exc))
    warmup = time.perf_counter()
    embedder.encode_one("Khởi động mô hình tra cứu văn bản")
    embedder.rerank("văn bản", ["Thông tin văn bản hành chính"])
    manifest = {"created_at": now_iso(), "snapshot": snapshot,
                "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
                "code_sha256": hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "src").rglob("*.py")))).hexdigest(),
                "models": {"main": config.LLM_MAIN, "cheap": config.LLM_CHEAP, "judge": args.judge_model},
                "deadline_seconds": config.AGENT_TIMEOUT_SECONDS, "max_output_tokens": config.LLM_MAX_OUTPUT_TOKENS, "warmup_seconds": time.perf_counter() - warmup,
                "categories": dict(Counter(c["category"] for c in cases)),
                "limitations": ["Pilot sample, not full corpus", "Synthetic questions; LLM judge is not human validation", "Single paired run, alternating order", "Not comparable to v1/v2 after corpus reset", "Warm in-process latency, excludes HTTP/concurrency"]}
    if previous is None:
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    else:
        append_jsonl(args.output / "resumes.jsonl", {"resumed_at": now_iso(), "warmup_seconds": manifest["warmup_seconds"]})
    by_pair = latest_results(args.output / "results.jsonl")
    consecutive_errors = 0
    for index, case in enumerate(cases):
        modes = ["always", "selective"] if index % 2 == 0 else ["selective", "always"]
        for mode in modes:
            pair = (case["id"], mode)
            if pair in by_pair and not by_pair[pair]["result"]["error"]:
                continue
            try:
                check_credit(args.minimum_credit_usd)
            except Exception as exc:
                (args.output / "status.json").write_text(json.dumps({"status": "paused", "reason": str(exc)}, ensure_ascii=False), encoding="utf-8")
                raise SystemExit(str(exc))
            started = time.perf_counter()
            with llm.track_usage() as usage:
                result = run_agent(case["question"], **case.get("filters", {}), routing_mode=mode)
            elapsed = (time.perf_counter() - started) * 1000
            expected = set(case.get("expected_documents", []))
            retrieved = list(dict.fromkeys(s["metadata"].get("doc_id") for s in result.sources))
            cited_ids = set(re.findall(r"\[(E\d+)\]", result.answer))
            cited_docs = {s["metadata"].get("doc_id") for s in result.sources if s["metadata"].get("evidence_id") in cited_ids}
            try:
                assessment = judge(case, result, args.judge_model)
            except Exception as exc:
                assessment = None
                print("[judge-error]", type(exc).__name__, flush=True)
            row = {"case_id": case["id"], "category": case["category"], "mode": mode,
                   "should_answer": case["should_answer"], "latency_ms": elapsed, "usage": dict(usage),
                   "expected_document_recall": len(expected & set(retrieved)) / len(expected) if expected else None,
                   "all_expected_cited": expected.issubset(cited_docs) if expected else None,
                   "result": result.model_dump(mode="json"), "judge": assessment}
            row["attempt"] = by_pair.get(pair, {}).get("attempt", 1) + 1 if pair in by_pair else 1
            by_pair[pair] = row
            append_jsonl(args.output / "results.jsonl", row)
            print(f"[benchmark] {index + 1}/{len(cases)} {mode} {result.route} {elapsed/1000:.1f}s calls={usage['calls']} error={result.error}", flush=True)
            consecutive_errors = consecutive_errors + 1 if result.error else 0
            if consecutive_errors >= 2:
                (args.output / "status.json").write_text(json.dumps({"status": "paused", "reason": "Two consecutive pipeline errors"}), encoding="utf-8")
                raise SystemExit("Paused after two pipeline errors; saved results are resumable")
    if corpus() != snapshot:
        raise RuntimeError("Corpus changed during benchmark; results must not be treated as paired comparison")
    summary = {}
    records = list(by_pair.values())
    for mode in ("always", "selective"):
        rows = [r for r in records if r["mode"] == mode]
        judged = [r for r in rows if r["judge"] is not None]
        answered = [r for r in rows if r["should_answer"]]
        negatives = [r for r in judged if not r["should_answer"]]
        summary[mode] = {"cases": len(rows), "errors": sum(bool(r["result"]["error"]) for r in rows),
                         "latency_p50_ms": percentile([r["latency_ms"] for r in rows], .5),
                         "latency_p95_ms": percentile([r["latency_ms"] for r in rows], .95),
                         "mean_llm_calls": mean([r["usage"]["calls"] for r in rows]),
                         "mean_reported_tokens": mean([r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in rows]),
                         "judge_coverage": len(judged) / len(rows),
                         "mean_correctness_0_2": mean([r["judge"]["correctness"] for r in judged]),
                         "mean_completeness_0_2": mean([r["judge"]["completeness"] for r in judged if isinstance(r["judge"].get("completeness"), (int, float))]),
                         "mean_expected_document_recall": mean([r["expected_document_recall"] for r in answered]),
                         "all_expected_cited_rate": mean([float(r["all_expected_cited"]) for r in answered]),
                         "negative_correctness_rate": mean([float(r["judge"]["correctness"] == 2) for r in negatives]),
                         "routes": dict(Counter(r["result"]["route"] for r in rows))}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Agentic V3 Paired Pilot", "", "Corpus: " + snapshot["fingerprint"], "", "| Metric | Always | Selective |", "|---|---:|---:|"]
    for key in summary["always"]:
        lines.append(f"| {key} | {summary['always'][key]} | {summary['selective'][key]} |")
    lines += ["", "Limitations:"] + ["- " + value for value in manifest["limitations"]]
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    (args.output / "status.json").write_text(json.dumps({"status": "complete", "pairs": len(records)}), encoding="utf-8")


if __name__ == "__main__":
    main()
