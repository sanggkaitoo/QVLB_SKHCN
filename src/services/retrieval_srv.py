"""Công cụ truy xuất cho agent: hybrid search nhiều khía cạnh, tra số ký hiệu, mở rộng ngữ cảnh."""
from __future__ import annotations

import contextvars
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from qdrant_client import models as qm

from src.core import config, embedder, store
from src.services.document_fields import (  # noqa: F401  (DOCUMENT_REF_RE re-exported for routing)
    DOCUMENT_REF_RE, extract_document_refs, issued_day, normalize_agency,
)

_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="qdrant")


def _parallel(function, argument_lists: list[tuple]) -> list:
    """Run I/O-bound calls concurrently, keeping each caller's deadline context."""
    if len(argument_lists) <= 1:
        return [function(*arguments) for arguments in argument_lists]
    futures = [_executor.submit(contextvars.copy_context().run, function, *arguments) for arguments in argument_lists]
    return [future.result() for future in futures]


def _collection(collection_name: str | None = None) -> str:
    return collection_name or config.RAG_COLLECTION


def qdrant_filter(filters: dict | None = None, doc_ids=None, kinds: list[str] | None = None) -> qm.Filter:
    filters = filters or {}
    conditions = [
        qm.FieldCondition(key="index_version", match=qm.MatchValue(value=config.INGEST_VERSION)),
        qm.FieldCondition(key="ready", match=qm.MatchValue(value=True)),
    ]
    for key in ("loai_vb", "huong", "linh_vuc"):
        values = filters.get(key)
        if values:
            values = [values] if isinstance(values, str) else list(values)
            conditions.append(qm.FieldCondition(key=key, match=qm.MatchAny(any=values)))
    if doc_ids is not None:
        conditions.append(qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=[int(d) for d in doc_ids] or [-1])))
    if kinds:
        conditions.append(qm.FieldCondition(key="chunk_kind", match=qm.MatchAny(any=kinds)))
    date_from, date_to = filters.get("date_from"), filters.get("date_to")
    if date_from or date_to:
        lower, upper = issued_day(date_from), issued_day(date_to)
        if (date_from and lower is None) or (date_to and upper is None):
            raise ValueError("Invalid date filter")
        conditions.append(qm.FieldCondition(key="issued_day", range=qm.Range(gte=lower, lte=upper)))
    if filters.get("co_quan_ban_hanh"):
        names = store.matching_agencies(normalize_agency(filters["co_quan_ban_hanh"]))
        conditions.append(qm.FieldCondition(key="agency_normalized", match=qm.MatchAny(any=names or ["__no_agency__"])))
    return qm.Filter(must=conditions)


def _passes_metadata_filters(payload: dict[str, Any], filters: dict | None = None) -> bool:
    filters = filters or {}
    issued = str(payload.get("ngay_ban_hanh") or "")[:10]
    if filters.get("date_from") and (not issued or issued < filters["date_from"]):
        return False
    if filters.get("date_to") and (not issued or issued > filters["date_to"]):
        return False
    if filters.get("co_quan_ban_hanh"):
        expected = normalize_agency(filters["co_quan_ban_hanh"])
        actual = normalize_agency(payload.get("co_quan_ban_hanh"))
        if not actual or expected not in actual:
            return False
    for key in ("loai_vb", "huong"):
        expected_values = filters.get(key)
        if expected_values:
            expected_values = [expected_values] if isinstance(expected_values, str) else expected_values
            if payload.get(key) not in expected_values:
                return False
    return True


def _item(hit: Any, score: float | None = None) -> dict[str, Any]:
    payload = dict(hit.payload or {})
    return {
        "id": str(getattr(hit, "id", "")),
        "text": payload.get("text", ""),
        "payload": payload,
        "score": float(score if score is not None else getattr(hit, "score", 0.0)),
    }


def rerank_passage(item: dict[str, Any]) -> str:
    """Passage for the cross-encoder: short document context + the chunk itself."""
    payload = item.get("payload") or {}
    if payload.get("chunk_kind") == "document_summary":
        return item["text"]
    context = " – ".join(str(value) for value in (payload.get("so_ky_hieu"), str(payload.get("trich_yeu") or "")[:120]) if value)
    if payload.get("section_path") and payload.get("section_path") != "Nội dung":
        context += f" – {payload['section_path'][:80]}"
    return f"[{context}]\n{item['text']}" if context else item["text"]


def _select(ranked: list[tuple[dict, float]], top_k: int, min_score: float, per_doc_cap: int) -> list[dict]:
    output: list[dict] = []
    per_doc: dict[int, int] = {}
    for item, score in ranked:
        if score < min_score:
            continue
        doc_id = int(item["payload"].get("doc_id") or 0)
        if per_doc.get(doc_id, 0) >= per_doc_cap:
            continue
        item["score"] = float(score)
        output.append(item)
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        if len(output) >= top_k:
            break
    return output


def multi_aspect_search(
    query: str,
    aspects: list[str] | None = None,
    filters: dict | None = None,
    top_k: int = 8,
    pool: int | None = None,
    min_score: float | None = None,
    doc_ids=None,
    collection_name: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid search cho câu hỏi và từng khía cạnh con.

    Mỗi khía cạnh được rerank với chính truy vấn của nó (một lần gọi reranker cho tất cả),
    sau đó mỗi khía cạnh được giữ một hạn mức kết quả để câu hỏi nhiều ý không bị một ý lấn át.
    ``payload['aspect_scores']`` ghi điểm tốt nhất theo từng khía cạnh để agent đánh giá độ phủ.
    """
    filters = filters or {}
    queries = [query]
    for aspect in aspects or []:
        aspect = " ".join(str(aspect).split())
        if aspect and aspect not in queries:
            queries.append(aspect)
    queries = queries[: max(1, config.RAG_MULTI_QUERY_COUNT)]
    pool = pool or config.RAG_RERANK_POOL
    per_query = max(8, math.ceil(pool / len(queries))) if len(queries) > 1 else pool
    threshold = config.RAG_MIN_RERANK_SCORE if min_score is None else min_score

    vectors = embedder.encode_queries(queries)
    flt = qdrant_filter(filters, doc_ids=doc_ids)
    collection = _collection(collection_name)
    hit_lists = _parallel(
        lambda vector: store.hybrid_query(vector["dense"], vector["sparse"], top_k=per_query, flt=flt,
                                          collection_name=collection),
        [(vector,) for vector in vectors],
    )
    candidates: dict[str, dict] = {}
    pairs: list[tuple[int, str]] = []
    for aspect_index, hits in enumerate(hit_lists):
        for hit in hits:
            item = candidates.setdefault(str(hit.id), _item(hit))
            pairs.append((aspect_index, str(hit.id)))
    if not candidates:
        return []
    ready = store.ready_doc_ids({item["payload"].get("doc_id") for item in candidates.values()})
    pairs = [(aspect, key) for aspect, key in pairs
             if candidates[key]["payload"].get("doc_id") in ready and _passes_metadata_filters(candidates[key]["payload"], filters)]
    if not pairs:
        return []
    scores = embedder.rerank_pairs([(queries[aspect], rerank_passage(candidates[key])) for aspect, key in pairs])

    best: dict[str, float] = {}
    by_aspect: dict[int, list[tuple[str, float]]] = {}
    for (aspect, key), score in zip(pairs, scores):
        candidates[key]["payload"].setdefault("aspect_scores", {})[str(aspect)] = round(float(score), 4)
        best[key] = max(best.get(key, 0.0), float(score))
        by_aspect.setdefault(aspect, []).append((key, float(score)))

    single_doc = doc_ids is not None and len(list(doc_ids)) == 1
    per_doc_cap = top_k if single_doc else config.RAG_MAX_CHUNKS_PER_DOC
    chosen: dict[str, dict] = {}
    if len(queries) > 1:
        quota = max(2, top_k // len(queries))
        for aspect in range(len(queries)):
            ranked = sorted(by_aspect.get(aspect, []), key=lambda pair: pair[1], reverse=True)
            picked = _select([(candidates[key], score) for key, score in ranked if key not in chosen],
                             quota, threshold, per_doc_cap)
            for item in picked:
                chosen[item["id"]] = item
    ranked_all = sorted(best.items(), key=lambda pair: pair[1], reverse=True)
    remaining_slots = max(0, top_k - len(chosen))
    for item in _select([(candidates[key], score) for key, score in ranked_all if key not in chosen],
                        remaining_slots, threshold, per_doc_cap):
        chosen[item["id"]] = item
    output = []
    for item in chosen.values():
        item["score"] = best[item["id"]]
        item["payload"]["retrieval_tool"] = "hybrid_search"
        output.append(item)
    return sorted(output, key=lambda item: item["score"], reverse=True)


def hybrid_search(query: str, top_k: int = 8, filters: dict | None = None, rerank_pool: int | None = None,
                  min_score: float | None = None, doc_ids=None, collection_name: str | None = None):
    return multi_aspect_search(query, [], filters, top_k=top_k, pool=rerank_pool, min_score=min_score,
                               doc_ids=doc_ids, collection_name=collection_name)


def _summary_candidate(document: dict[str, Any]) -> dict[str, Any]:
    doc_id = int(document["id"])
    summary = " ".join([
        f"Số ký hiệu: {document.get('so_ky_hieu') or 'không rõ'}.",
        f"Ngày ban hành: {document.get('ngay_ban_hanh') or 'không rõ'}.",
        f"Cơ quan ban hành: {document.get('co_quan_ban_hanh') or 'không rõ'}.",
        f"Trích yếu: {document.get('trich_yeu') or 'không có trích yếu'}.",
    ])
    metadata = document_metadata(document)
    return {
        "id": f"postgres-summary:{doc_id}",
        "text": summary,
        "payload": {**metadata, "text": summary, "chunk_kind": "document_summary", "chunk_index": -1},
        "score": 1.0,
    }


def document_metadata(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": int(document["id"]),
        "so_ky_hieu": document.get("so_ky_hieu"),
        "ngay_ban_hanh": str(document.get("ngay_ban_hanh") or ""),
        "loai_vb": document.get("loai_vb"),
        "huong": document.get("huong"),
        "co_quan_ban_hanh": document.get("co_quan_ban_hanh"),
        "trich_yeu": document.get("trich_yeu"),
        "source_url": document.get("source_url"),
        "tinh_trang_hieu_luc": document.get("tinh_trang_hieu_luc"),
        "hieu_luc_tu": str(document.get("hieu_luc_tu") or ""),
        "hieu_luc_den": str(document.get("hieu_luc_den") or ""),
    }


def resolve_documents(refs: list[str], filters: dict | None = None, limit_per_ref: int = 3) -> list[dict]:
    documents: list[dict] = []
    seen: set[int] = set()
    for ref in refs:
        for document in store.search_documents_by_reference(ref, limit=limit_per_ref):
            if not _passes_metadata_filters(document, filters):
                continue
            if int(document["id"]) not in seen:
                documents.append(dict(document))
                seen.add(int(document["id"]))
    return documents


def exact_document_search(query: str, refs: list[str] | None = None, filters: dict | None = None,
                          top_k: int = 8, collection_name: str | None = None):
    """Văn bản được nêu số ký hiệu: bản tóm tắt từ PostgreSQL + đoạn liên quan nhất trong từng văn bản."""
    refs = refs if refs is not None else extract_document_refs(query)
    documents = resolve_documents(refs, filters)[:4]
    if not documents:
        return []
    vector = embedder.encode_one(query)
    collection = _collection(collection_name)
    chunk_lists = _parallel(
        lambda doc_id: store.hybrid_query(vector["dense"], vector["sparse"], top_k=12,
                                          flt=qdrant_filter(doc_ids=[doc_id], kinds=["child"]),
                                          collection_name=collection),
        [(int(document["id"]),) for document in documents],
    )
    summaries, candidates = [], []
    for document, chunks in zip(documents, chunk_lists):
        summaries.append(_summary_candidate(document))
        common = document_metadata(document)
        for chunk in chunks:
            candidate = _item(chunk)
            candidate["payload"] = {**common, **candidate["payload"]}
            candidates.append(candidate)
    scores = embedder.rerank_pairs([(query, rerank_passage(item)) for item in summaries + candidates])
    for item, score in zip(summaries, scores[:len(summaries)]):
        item["score"] = max(float(score), config.RAG_FAST_MIN_SCORE)  # văn bản được nêu tên luôn là bằng chứng
        item["payload"]["retrieval_tool"] = "exact_document_search"
    per_doc_cap = top_k if len(documents) == 1 else max(2, math.ceil(top_k / len(documents)))
    ranked = sorted(zip(candidates, scores[len(summaries):]), key=lambda pair: pair[1], reverse=True)
    chunks = _select(ranked, max(0, top_k - len(summaries)), config.RAG_MIN_RERANK_SCORE, per_doc_cap)
    for item in chunks:
        item["payload"]["retrieval_tool"] = "exact_document_search"
    return summaries + chunks


def retrieve_neighbors(items: list[dict[str, Any]], window: int = 1, collection_name: str | None = None):
    output = list(items)
    seen = {(item["payload"].get("doc_id"), item["payload"].get("file_id"), item["payload"].get("chunk_index"))
            for item in output}
    grouped: dict[tuple, dict] = {}
    for item in items:
        payload = item["payload"]
        doc_id, chunk_index = payload.get("doc_id"), payload.get("chunk_index")
        if doc_id is None or chunk_index is None or int(chunk_index) < 0:
            continue
        group = grouped.setdefault((int(doc_id), payload.get("file_id")), {"indices": set(), "score": 0.0})
        group["indices"].update(range(max(0, int(chunk_index) - window), int(chunk_index) + window + 1))
        group["score"] = max(group["score"], float(item["score"]))
    collection = _collection(collection_name)
    keys = list(grouped)
    fetched = _parallel(
        lambda doc_id, file_id: store.get_chunks(doc_id, file_id=file_id, indices=sorted(grouped[(doc_id, file_id)]["indices"]),
                                                 collection_name=collection),
        keys,
    )
    for (doc_id, file_id), points in zip(keys, fetched):
        for point in points:
            candidate = _item(point, max(0.0, grouped[(doc_id, file_id)]["score"] - 0.05))
            key = (doc_id, candidate["payload"].get("file_id"), candidate["payload"].get("chunk_index"))
            if key in seen or key[2] is None or not candidate["payload"].get("ready"):
                continue
            candidate["payload"]["retrieval_tool"] = "retrieve_neighbors"
            output.append(candidate)
            seen.add(key)
    return output


def _window(parent_text: str, child: str, max_chars: int) -> str:
    if len(parent_text) <= max_chars:
        return parent_text
    position = max(0, parent_text.find(child))
    start = max(0, min(position - (max_chars - len(child)) // 2, len(parent_text) - max_chars))
    return parent_text[start:start + max_chars]


def retrieve_parent_section(items: list[dict[str, Any]], max_parents: int = 4, max_chars: int = 2500):
    """Thay các đoạn hàng đầu bằng ngữ cảnh mục cha (Điều/Khoản) quanh đoạn đó; giới hạn độ dài."""
    output = []
    seen_parent_ids: set[str] = set()
    for item in items:
        payload = item["payload"]
        parent_id = str(payload.get("parent_chunk_id") or "")
        parent_text = str(payload.get("parent_text") or "")
        if (not parent_id or not parent_text or item["text"] not in parent_text
                or len(parent_text) <= len(item["text"]) + 50):
            output.append(item)
            continue
        if parent_id in seen_parent_ids:
            continue
        if len(seen_parent_ids) >= max_parents:
            output.append(item)
            continue
        text = _window(parent_text, item["text"], max_chars)
        parent_payload = dict(payload)
        parent_payload.update({"text": text, "chunk_kind": "parent", "retrieval_tool": "retrieve_parent_section"})
        output.append({
            "id": f"parent:{parent_id}",
            "text": text,
            "payload": parent_payload,
            "score": max(0.0, float(item["score"]) - 0.03),
        })
        seen_parent_ids.add(parent_id)
    return output


def find_document_relations(doc_ids) -> list[dict[str, Any]]:
    return store.get_document_relations(doc_ids)
