# Agentic RAG v4 operations

## Active configuration

- Index version `agentic-v4`, collection `RAG_COLLECTION=docnexus_agentic_v4`.
- `AGENT_ROUTING_MODE=selective` (default) lets rules decide when an LLM planner/grader is needed; `always` forces both for paired comparison.
- `/api/search_stream` (alias `/api/search_agent_stream`) streams Server-Sent Events; `/api/search` returns the final JSON result.
- `/api/health` reports engine, collection and index version without loading models.
- `run.sh` starts databases, applies migrations and prepares the collection. It never resets data.

## Data model

- `documents` is one văn bản (identified by `source_key`: direction + crawler history key, or `sha256:` for single uploads).
- `document_files` holds each file with `role`: `chinh` (main text), `ban_sao` (near-identical copy, not indexed, `duplicate_of` points at the indexed file) or `dinh_kem` (attachment, indexed).
- Duplicate detection uses word 3-gram containment ≥ `DUPLICATE_FILE_SIMILARITY` (0.8) between files of similar length (ratio ≥ 0.6) within a document; the cleanest extraction (text PDF/DOCX over OCR) represents the cluster.
- LLM metadata is extracted once per document from the main file; crawler metadata wins after placeholder cleanup, and references split by digital-signature layout are rebuilt from the "Số: …" header line.
- `document_relations` is filled at ingest by rules (căn cứ, sửa đổi, thay thế, bãi bỏ, liên quan) with `verified=false`; verified relations are never overwritten.

## Ingestion and retrieval

- PDF extraction preserves page markers and selectively OCRs scanned pages; DOCX tables keep order and repeat header context. `extract()` raises `ExtractionError` with the cause instead of returning empty text.
- Chunking uses the embedding tokenizer (512 tokens, 64 overlap). Each chunk is embedded with a document header (reference, type, subject, attachment name) and section path; each document also gets one `document_summary` point.
- Writes are tagged with an `ingest_run`; stale points are deleted and the document is published only after the new run is fully written.
- Retrieval filters (lists for type/direction/field, date range, agency) are applied inside Qdrant. Multi-aspect search reranks every aspect against its own query in one reranker call and keeps a per-aspect quota.
- Neighbours are fetched per file; parent sections are bounded to 2,500 characters around the child.

## Agent loop and streaming

1. Rule analysis (intent, filters, references including `57-NQ/TW` and unique short numbers).
2. Metadata templates answered directly from PostgreSQL.
3. LLM planning only for complex questions (or `always` mode), exactly once.
4. Retrieval → rule coverage gate (top score, named documents, each aspect). If insufficient, one LLM call grades the evidence and proposes follow-up queries.
5. Answer tokens stream to the client; the verifier then labels each segment and the `answer` event replaces the draft with the verified text (unsupported segments removed, Markdown kept). Verifier failure withholds the draft.

Capacity: at most `RAG_CONCURRENCY` heavy requests; the stream reserves capacity before responding (HTTP 503 when full) and releases it when the worker finishes even if the client disconnects. Keep-alive comments are sent every 15 seconds.

## Aggregation v2

See the README section. Key limits: `AGG_MAX_DOCS`, `AGG_CHUNKS_PER_DOC`, `AGG_CONCURRENCY`, `AGG_MIN_SCORE` (document relevance gate), `AGG_MIN_CONFIDENCE`, `AGG_TIMEOUT_SECONDS`. Results always list included facts with verbatim quotes and a review list with reasons.

## Upgrade, reindex and reset

```bash
docker exec qlvb_postgres pg_dump -U qlvb qlvb > backup-before-v4.sql
./venv/bin/python scripts/apply_migrations.py
./venv/bin/python scripts/reindex.py            # reuses stored text + metadata
./venv/bin/python scripts/reindex.py --start-id 500 --re-extract
```

The reset command remains destructive and restricted to the local `qlvb` database and project collections:

```bash
./venv/bin/python scripts/reset_agentic_data.py --confirm-delete-qlvb
```

## Evaluation

Historical v1/v2/v3 reports are untouched. v3 numbers (P50 5.99 s selective, P95 ≈ 43 s) predate streaming, grouping and the compact verifier; re-run `scripts/benchmark_selective.py` on the v4 index before comparing. Per-stage timings are stored in `rag_query_logs.plan.timings`.
