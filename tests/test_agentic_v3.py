import asyncio
import re
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.chunking import build_structured_chunks
from src.services import retrieval_srv as retrieval
from src.agent.query_analyzer import detect_filters, detect_intent
from src.agent.schemas import Evidence, QueryIntent
from src.agent.verifier import verify_answer
from src.core import runtime


def word_tokenizer(text, **kwargs):
    return {"offset_mapping": [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]}


class AgenticV3Tests(unittest.TestCase):
    def test_chunks_have_page_token_limit_and_containing_parent(self):
        text = "[[PAGE 1]]\nĐiều 1. Nội dung\n" + " ".join(f"word{i}" for i in range(2000)) + "\n[[PAGE 2]]\nKết thúc."
        chunks = build_structured_chunks(text, 64, 8, "test", max_parent_chars=700, tokenizer=word_tokenizer)
        self.assertTrue(chunks)
        self.assertEqual({1, 2}, {c.page_start for c in chunks})
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text.split()), 64)
            self.assertIn(chunk.text, chunk.parent_text)
        self.assertNotEqual(chunks[0].parent_text, chunks[-2].parent_text)

    def test_compare_refs_is_not_exact_lookup(self):
        self.assertEqual(QueryIntent.COMPARE, detect_intent("So sánh 12/QĐ-UBND và 15/QĐ-UBND"))
        self.assertNotIn("date_from", detect_filters("Theo 12/2024/NĐ-CP thì sao?"))
        self.assertEqual(["12/2024/ND-CP"], retrieval.extract_document_refs("Theo 12/2024/NĐ-CP thì sao?"))
        self.assertEqual("2026-01-01", detect_filters("văn bản trong năm 2026")["date_from"])

    def test_missing_agency_does_not_match(self):
        self.assertFalse(retrieval._passes_metadata_filters({}, co_quan_ban_hanh="UBND tỉnh"))

    def test_filters_are_applied_before_vector_search(self):
        with patch.object(retrieval.store, "matching_agencies", return_value=["uy ban nhan dan tinh"]):
            filters = retrieval._filter(date_from="2026-01-01", co_quan_ban_hanh="UBND tỉnh")
        self.assertTrue({"issued_day", "agency_normalized", "ready", "index_version"}.issubset({item.key for item in filters.must}))

    def test_multiquery_reranks_once(self):
        hit = SimpleNamespace(id="one", score=0.5, payload={"text": "Evidence", "doc_id": 1})
        with patch.object(retrieval.embedder, "encode", return_value=[{"dense": [], "sparse": {}}] * 2), \
             patch.object(retrieval.embedder, "rerank", return_value=[0.9]) as rerank, \
             patch.object(retrieval.store, "hybrid_query", return_value=[hit]), \
             patch.object(retrieval.store, "ready_doc_ids", return_value={1}):
            result = retrieval.multi_query_search("question", ["alternative"])
        self.assertEqual(1, rerank.call_count)
        self.assertEqual(1, len(result))

    def test_neighbors_fetch_once_per_document(self):
        items = [{"id": str(i), "text": "text", "score": 0.9, "payload": {"doc_id": 1, "chunk_index": i}} for i in (5, 6)]
        with patch.object(retrieval.store, "get_chunks_for_doc", return_value=[]) as fetch:
            retrieval.retrieve_neighbors(items)
        self.assertEqual(1, fetch.call_count)
        self.assertEqual([4, 5, 6, 7], fetch.call_args.kwargs["indices"])

    def test_verifier_never_returns_unverified_draft(self):
        evidence = [Evidence(evidence_id="E1", text="source", score=0.9)]
        with patch("src.agent.verifier.llm.extract_json", side_effect=RuntimeError("offline")):
            result = verify_answer("question", "invented fact [E1]", evidence)
        self.assertNotIn("invented fact", result.answer)
        self.assertEqual("thap", result.confidence)

    def test_blocking_work_does_not_block_event_loop(self):
        async def scenario():
            release, entered = threading.Event(), threading.Event()
            def work():
                entered.set()
                release.wait(2)
            job = asyncio.create_task(runtime.run_blocking(work))
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                self.assertFalse(job.done())
                await asyncio.sleep(0)
            finally:
                release.set()
                await job
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
