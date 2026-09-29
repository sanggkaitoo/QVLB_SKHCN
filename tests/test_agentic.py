import asyncio
import re
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from src.agent.query_analyzer import detect_filters, detect_intent
from src.agent.schemas import QueryIntent
from src.core import runtime
from src.services import retrieval_srv as retrieval
from src.services.chunking import build_structured_chunks


def word_tokenizer(text, **kwargs):
    return {"offset_mapping": [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]}


def hit(point_id, doc_id, text="Evidence", **payload):
    return SimpleNamespace(id=point_id, score=0.5, payload={"text": text, "doc_id": doc_id, **payload})


class AgenticTests(unittest.TestCase):
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
        self.assertFalse(retrieval._passes_metadata_filters({}, {"co_quan_ban_hanh": "UBND tỉnh"}))

    def test_filters_are_applied_before_vector_search(self):
        with patch.object(retrieval.store, "matching_agencies", return_value=["uy ban nhan dan tinh"]):
            flt = retrieval.qdrant_filter({"date_from": "2026-01-01", "co_quan_ban_hanh": "UBND tỉnh",
                                           "loai_vb": ["bao_cao", "ke_hoach"], "linh_vuc": ["cds"]})
        keys = {item.key for item in flt.must}
        self.assertTrue({"issued_day", "agency_normalized", "ready", "index_version", "loai_vb", "linh_vuc"} <= keys)
        loai = next(item for item in flt.must if item.key == "loai_vb")
        self.assertEqual(["bao_cao", "ke_hoach"], loai.match.any)

    def test_multi_aspect_reranks_once_with_each_aspect_query(self):
        with patch.object(retrieval.embedder, "encode_queries", return_value=[{"dense": [], "sparse": {}}] * 2), \
             patch.object(retrieval.embedder, "rerank_pairs", side_effect=lambda pairs: [0.9] * len(pairs)) as rerank, \
             patch.object(retrieval.store, "hybrid_query", side_effect=[[hit("one", 1)], [hit("two", 2)]]), \
             patch.object(retrieval.store, "ready_doc_ids", return_value={1, 2}):
            result = retrieval.multi_aspect_search("question", ["alternative"])
        self.assertEqual(1, rerank.call_count)
        queries = [pair[0] for pair in rerank.call_args.args[0]]
        self.assertEqual(["question", "alternative"], queries)
        self.assertEqual(2, len(result))
        self.assertEqual({"0": 0.9}, next(item for item in result if item["id"] == "one")["payload"]["aspect_scores"])

    def test_aspect_quota_keeps_second_aspect(self):
        first = [hit(f"a{i}", 10 + i) for i in range(8)]
        second = [hit("b0", 99)]

        def scores(pairs):
            return [0.95 if query == "question" else 0.4 for query, _ in pairs]
        with patch.object(retrieval.embedder, "encode_queries", return_value=[{"dense": [], "sparse": {}}] * 2), \
             patch.object(retrieval.embedder, "rerank_pairs", side_effect=scores), \
             patch.object(retrieval.store, "hybrid_query", side_effect=[first, second]), \
             patch.object(retrieval.store, "ready_doc_ids", return_value=set(range(100))):
            result = retrieval.multi_aspect_search("question", ["other aspect"], top_k=4)
        self.assertIn("b0", {item["id"] for item in result})

    def test_neighbors_fetch_once_per_file(self):
        items = [{"id": str(i), "text": "text", "score": 0.9, "payload": {"doc_id": 1, "file_id": 7, "chunk_index": i}}
                 for i in (5, 6)]
        with patch.object(retrieval.store, "get_chunks", return_value=[]) as fetch:
            retrieval.retrieve_neighbors(items)
        self.assertEqual(1, fetch.call_count)
        self.assertEqual(7, fetch.call_args.kwargs["file_id"])
        self.assertEqual([4, 5, 6, 7], fetch.call_args.kwargs["indices"])

    def test_parent_section_is_bounded_around_child(self):
        parent = "A" * 5000 + " CHILD TEXT " + "B" * 5000
        items = [{"id": "c", "text": "CHILD TEXT", "score": 0.8,
                  "payload": {"parent_chunk_id": "p", "parent_text": parent}}]
        output = retrieval.retrieve_parent_section(items, max_chars=1000)
        self.assertEqual(1000, len(output[0]["text"]))
        self.assertIn("CHILD TEXT", output[0]["text"])

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
            finally:
                release.set()
                await job
        asyncio.run(scenario())

    def test_event_stream_releases_capacity_and_rejects_when_full(self):
        async def scenario():
            slots = threading.BoundedSemaphore(1)
            release = threading.Event()

            def events():
                yield {"type": "status"}
                release.wait(2)
                yield {"type": "done"}
            stream = runtime.open_event_stream(events, slots=slots)
            with self.assertRaises(HTTPException) as busy:
                runtime.open_event_stream(events, slots=slots)
            self.assertEqual(503, busy.exception.status_code)
            first = await stream.__anext__()
            self.assertEqual("status", first["type"])
            release.set()
            received = [event async for event in stream]
            self.assertEqual([{"type": "done"}], received)
            for _ in range(100):
                if slots.acquire(blocking=False):
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("capacity was not released")
        asyncio.run(scenario())

    def test_sse_format(self):
        self.assertEqual('event: token\ndata: {"type": "token", "text": "ạ"}\n\n', runtime.sse({"type": "token", "text": "ạ"}))
        self.assertEqual(": keepalive\n\n", runtime.sse(runtime.KEEPALIVE))


if __name__ == "__main__":
    unittest.main()
