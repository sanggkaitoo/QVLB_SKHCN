# Agentic RAG v3 operations

## Active configuration

- Both `/api/search_stream` and `/api/search_agent_stream` use the agent with the default configuration.
- `AGENTIC_RAG_ENABLED=true`, `RAG_COLLECTION=docnexus_agentic_v3`.
- `AGENT_ROUTING_MODE=selective` is the default; `always` runs the full agent loop for comparison.
- `/api/health` reports engine, collection and index version without loading models.
- `run.sh` starts databases, applies migrations and prepares the collection. It never resets data.

## Ingestion and retrieval

- PDF extraction preserves page markers and selectively OCRs scanned pages, one page in memory at a time.
- DOCX tables retain document order. Table and spreadsheet rows repeat header context.
- Chunking uses the embedding tokenizer: 512 tokens, 64 overlap by default. Parent windows surround the actual child, not the beginning of the document.
- Embedding includes the document reference and section path; writes are batched with `EMBED_BATCH_SIZE=8`.
- SHA-256 deduplication happens before extraction/LLM metadata calls. Failed files stay retryable.
- Only ready documents from the current index version are retrieved. Pending/failed vector writes are excluded.
- Reference lookup uses exact normalized PostgreSQL matches. Date/agency filters apply before vector candidate selection.
- Multi-query retrieval fuses candidates with RRF and reranks the merged pool once. Neighbor retrieval is grouped by document and indexed chunk numbers.

## Resource limits and verification

- Heavy RAG preparation runs outside the web event loop, at most `RAG_CONCURRENCY=2` requests concurrently; excess requests receive HTTP 503 and Retry-After.
- Model inference is serialized per process, with configurable CPU threads and batch size. PostgreSQL connections use a bounded pool.
- Agent attempts are bounded and evidence accumulates across attempts. The grader identifies missing requirements before a query rewrite.
- A cooperative request deadline bounds subsequent work and network calls. Native model computations already running are not forcibly killed.
- Answers are released only after verification; this is not token streaming during draft generation.
- Verifier failures do not release an unverified draft. Confidence is conservative, not a calibrated probability.
- Exact, unambiguous metadata question templates can be answered directly from PostgreSQL with a source, without an LLM call. Arbitrary questions containing a reference do not qualify.
- Simple questions with strong retrieval may skip the initial grader only after answer verification confirms complete supported coverage. Otherwise the existing evidence is reused by the full agent; complex questions use the agent directly.
- The `route` and `fallback_reason` are stored inside query log plans. Both HTTP search endpoints follow the configured mode; the paired benchmark overrides the mode per call.
- Numeric aggregation remains experimental and is not a validated financial/statistical reporting engine.

## Reset and re-import

The explicit reset command is destructive and restricted to the local `qlvb` database and project collections:

```bash
./venv/bin/python scripts/reset_agentic_data.py --confirm-delete-qlvb
```

It clears document/relationship/query data, the project vector collections and crawler checkpoints, while preserving schemas, original source files and filesystem baseline reports. Stop web/crawl before invoking it. No old-data rollback is available without an independent backup.

After reset, crawl/import documents again. Re-extraction of already registered source files can be resumed by document id:

```bash
./venv/bin/python scripts/reindex_v2.py --limit 1
./venv/bin/python scripts/reindex_v2.py --start-id 500
```

Despite its historical filename, this script uses the active v3 ingestion pipeline and original files, not old extracted text. An empty database has no registered source files to reindex.

## Evaluation

Existing v1/v2 baseline reports are historical and remain untouched. The paired v3 pilot on 30 retained documents (94 vectors) and 18 frozen questions is complete: 36 final pipeline results without errors, 35 with an LLM judge score. See [the report](../reports/baseline-agent-v3-selective.md) and [saved-run status](../reports/baseline-agent-v3-status.md). Selective routing reduced P50 from 8.04 to 5.99 seconds and mean AI calls from 3.33 to 2.00; P95 was nearly unchanged (43.61 versus 43.09 seconds). Results span the initial run and a credit-funded resume; earlier failed attempts are retained.

Do not interpret the raw judge average as a proven accuracy improvement: identical abstentions received inconsistent scores, and one judge response exceeded its token cap. All six no-answer outputs abstained without citing sources. This pilot does not validate full-corpus performance, concurrent load, Cloudflare latency, or numeric aggregation. Reset changes IDs and the corpus snapshot, so v1/v2 scores are not a controlled comparison with v3.

Use `scripts/benchmark_selective.py --input <frozen-questions.jsonl> --output <new-directory>` for the paired v3 evaluation. It refuses to overwrite a run, fingerprints the corpus/code/cases, alternates execution order, captures reported token usage and judges answers separately. Source files for a bounded pilot can be imported with `scripts/import_eval_sample.py --count 30 --output <new-manifest.jsonl>`; this prefers short text PDFs/DOCX and is not representative of scans or large tables.
