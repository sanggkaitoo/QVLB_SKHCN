# Agentic RAG baseline v2

Snapshot after implementing exact lookup, structured retrieval, bounded agent attempts,
answer verification, model fallback, and correct abstention. Generated case-level content stays
under ignored paths.

## Retrieval-only final

- Dataset: the same 10 cases used by `reports/baseline-v1.md`
- Answerable cases: 8
- Exact lookup cases: 4/4 at rank 1
- Semantic cases: ranks 1, 8, 3, and 1
- Correct no-answer retrieval: 2/2

| Metric | RAG v1 | Agentic v2 |
|---|---:|---:|
| Hit@1 | 25.0% | 75.0% |
| Hit@5 | 50.0% | 87.5% |
| Hit@10 | 62.5% | 100.0% |
| MRR | 0.37 | 0.807 |
| Correct abstention | 50.0% | 100.0% |

## Full pipeline run

The final full answer run before the top-6-to-8 preservation fix produced:

| Metric | RAG v1 | Agentic v2 |
|---|---:|---:|
| Answer availability | 70.0% | 100.0% |
| Expected source cited | 25.0% | 50.0% |
| Judge correctness (0-2) | 0.29 | 1.20 |
| Judge completeness (0-2) | 0.29 | 1.20 |
| Grounded claim ratio | 14.3% | 70.0% |
| P95 total latency | 13.8 s | 66.1 s |

The retrieval fix after this full run only restores a missing rank-8 source; it does not alter
the answer model or verifier. Latency remains a release blocker. Keep `AGENTIC_RAG_ENABLED=false`
for the main endpoint until the v2 collection is fully indexed and a larger evaluation confirms
quality and latency.
