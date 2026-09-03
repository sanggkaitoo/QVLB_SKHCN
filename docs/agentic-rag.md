# Agentic RAG operations

## Components

- Legacy endpoint: `/api/search_stream`
- Agentic A/B endpoint: `/api/search_agent_stream`
- Feature flag: `AGENTIC_RAG_ENABLED`
- Legacy collection: `qlvb_docs`
- Structured collection: `qlvb_docs_v2`
- Baselines: `reports/baseline-v1.md` and `reports/baseline-agent-v2.md`

## Database migration

```bash
./venv/bin/python scripts/apply_migrations.py
```

Migrations are idempotent and tracked in `schema_migrations` by checksum.

## Re-index without overwriting v1

Smoke test:

```bash
./venv/bin/python scripts/reindex_v2.py --limit 1
```

Full re-index, resumable by document id:

```bash
./venv/bin/python scripts/reindex_v2.py --collection qlvb_docs_v2
./venv/bin/python scripts/reindex_v2.py --collection qlvb_docs_v2 --start-id 500
```

Point IDs are deterministic, so rerunning a document is idempotent. Do not set
`RAG_COLLECTION=qlvb_docs_v2` until the full re-index has finished and point/document counts have
been checked.

## Evaluation

```bash
./venv/bin/python scripts/run_agent_retrieval_baseline.py
./venv/bin/python scripts/run_baseline_api.py \
  --endpoint /api/search_agent_stream \
  --output reports/generated/baseline-agent-v2.jsonl \
  --judge --overwrite
./venv/bin/python scripts/report_baseline.py \
  --input reports/generated/baseline-agent-v2.jsonl \
  --output reports/generated/baseline-agent-v2.md
```

## Rollout and rollback

After full indexing and evaluation:

```env
RAG_COLLECTION=qlvb_docs_v2
AGENTIC_RAG_ENABLED=true
```

Rollback requires only:

```env
AGENTIC_RAG_ENABLED=false
```

The legacy collection and endpoint are not deleted or overwritten.
