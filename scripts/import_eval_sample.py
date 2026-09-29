"""Bounded, reproducible import of retained sources for a pilot evaluation."""
import argparse
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fitz
from src.core import config
from src.services.ingest import ingest_file
from scripts._baseline_common import append_jsonl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("Sample count must be between 1 and 100")
    files = sorted(p for p in Path(config.STORE_DIR).rglob("*") if p.is_file()
                   and p.suffix.lower() in {".docx", ".pdf"} and "failed_ingest" not in p.parts
                   and p.stat().st_size < 2_000_000)
    random.Random(args.seed).shuffle(files)
    selected = []
    for path in files:
        if path.suffix.lower() == ".pdf":
            try:
                with fitz.open(path) as doc:
                    if len(doc) > 6 or sum(len(page.get_text()) for page in doc) < 900:
                        continue
            except Exception:
                continue
        selected.append(path)
        if len(selected) >= args.count:
            break
    failures = 0
    for index, path in enumerate(selected, 1):
        sidecar = Path(str(path) + ".meta.json")
        metadata = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        try:
            doc_id = ingest_file(str(path), huong=metadata.get("huong") or "di", raw_meta=metadata, source_url=metadata.get("source_url"))
            append_jsonl(args.output, {"path": str(path), "doc_id": doc_id, "status": "ready", "seed": args.seed})
            print(f"[sample] {index}/{len(selected)} ready doc_id={doc_id}", flush=True)
        except Exception as exc:
            failures += 1
            append_jsonl(args.output, {"path": str(path), "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            print(f"[sample] {index}/{len(selected)} failed: {type(exc).__name__}: {exc}", flush=True)
    print(f"Imported {len(selected) - failures}/{len(selected)}. This is a pilot corpus, not the full archive.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
