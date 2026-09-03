# RAG baseline v1

Snapshot before the Agentic RAG upgrade. This file intentionally contains only aggregate
metrics; generated questions, answers, and document excerpts remain local under ignored paths.

- Measured at: `2026-07-23T01:01:55+07:00`
- Dataset: 10 synthetic, evidence-backed pilot questions
- Collection: `qlvb_docs`
- Embedding: `BAAI/bge-m3`
- Reranker: `BAAI/bge-reranker-v2-m3`
- Answer model: `qwen/qwen-2.5-72b-instruct`
- Judge model: `google/gemini-2.5-pro`

| Metric | Baseline |
|---|---:|
| Hit@1 | 25.0% |
| Hit@5 | 50.0% |
| Hit@10 | 62.5% |
| Recall@10 | 62.5% |
| MRR | 0.37 |
| Expected source cited | 25.0% |
| Answer availability | 70.0% |
| Correct abstention | 50.0% |
| Judge correctness (0-2) | 0.29 |
| Judge completeness (0-2) | 0.29 |
| Grounded claim ratio | 14.3% |

Category retrieval:

- Exact document lookup Hit@5: 25.0%
- Semantic QA Hit@5: 75.0%

The next evaluation must reuse `tests/eval/generated_questions.jsonl` from the local workspace
and compare against this snapshot. Because the pilot contains only ten questions, changes must
also be reviewed on a larger set before production rollout.
