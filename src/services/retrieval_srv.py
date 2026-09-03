from __future__ import annotations

import hashlib
import re
from typing import Any

from qdrant_client import models as qm

from src.core import config, embedder, store
from src.services.chunking import normalize_document_ref


DOCUMENT_REF_RE = re.compile(
    r"(?<!\w)(\d{1,6}\s*/\s*[0-9A-ZÀ-ỸĐ][0-9A-ZÀ-ỸĐ.\-]*(?:\s*-\s*[0-9A-ZÀ-ỸĐ.\-]+)*)(?!\w)",
    re.IGNORECASE,
)


def extract_document_refs(query: str) -> list[str]:
    refs: list[str] = []
    for match in DOCUMENT_REF_RE.finditer(query or ""):
        value = normalize_document_ref(match.group(1))
        if value and value not in refs:
            refs.append(value)
    return refs


def _collection(collection_name: str | None = None) -> str:
    requested = collection_name or config.RAG_COLLECTION
    return requested if store.collection_exists(requested) else config.QDRANT_COLLECTION


def _filter(loai_vb: str | None = None, huong: str | None = None, doc_ids: list[int] | None = None):
    conditions: list[qm.FieldCondition] = []
    if loai_vb:
        conditions.append(qm.FieldCondition(key="loai_vb", match=qm.MatchValue(value=loai_vb)))
    if huong:
        conditions.append(qm.FieldCondition(key="huong", match=qm.MatchValue(value=huong)))
    if doc_ids:
        conditions.append(qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=doc_ids)))
    return qm.Filter(must=conditions) if conditions else None


def _passes_metadata_filters(
    payload: dict[str, Any],
    date_from: str | None = None,
    date_to: str | None = None,
    co_quan_ban_hanh: str | None = None,
) -> bool:
    issued = str(payload.get("ngay_ban_hanh") or "")[:10]
    if date_from and (not issued or issued < date_from):
        return False
    if date_to and (not issued or issued > date_to):
        return False
    if co_quan_ban_hanh:
        expected = " ".join(co_quan_ban_hanh.lower().split())
        actual = " ".join(str(payload.get("co_quan_ban_hanh") or "").lower().split())
        if expected not in actual and actual not in expected:
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


def exact_document_search(query: str, top_k: int = 8, collection_name: str | None = None):
    refs = extract_document_refs(query)
    if not refs:
        return []
    documents: list[dict[str, Any]] = []
    seen_docs: set[int] = set()
    for ref in refs:
        for document in store.search_documents_by_reference(ref, limit=5):
            doc_id = int(document["id"])
            if doc_id not in seen_docs:
                documents.append(dict(document))
                seen_docs.add(doc_id)

    candidates: list[dict[str, Any]] = []
    active_collection = _collection(collection_name)
    for document in documents:
        doc_id = int(document["id"])
        summary_parts = [
            f"Số ký hiệu: {document.get('so_ky_hieu') or 'không rõ'}.",
            f"Ngày ban hành: {document.get('ngay_ban_hanh') or 'không rõ'}.",
            f"Trích yếu: {document.get('trich_yeu') or 'không có trích yếu'}.",
        ]
        summary_text = " ".join(summary_parts)
        common_metadata = {
            "doc_id": doc_id,
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
        candidates.append({
            "id": f"postgres-summary:{doc_id}",
            "text": summary_text,
            "payload": {**common_metadata, "text": summary_text, "chunk_kind": "document_summary"},
            "score": 1.0,
        })
        chunks = store.get_chunks_for_doc(doc_id, active_collection)
        for chunk in chunks:
            candidate = _item(chunk)
            candidate["payload"] = {**common_metadata, **candidate["payload"]}
            candidates.append(candidate)
        if not chunks and document.get("full_text"):
            full_text = str(document["full_text"])[:4000]
            candidates.append({
                "id": f"postgres-body:{doc_id}",
                "text": full_text,
                "payload": {**common_metadata, "text": full_text, "chunk_kind": "document_body"},
                "score": 1.0,
            })
    if not candidates:
        return []

    passages = [candidate["text"] for candidate in candidates]
    scores = embedder.rerank(query, passages)
    ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
    output: list[dict[str, Any]] = []
    per_doc: dict[int, int] = {}
    for candidate, score in ranked:
        doc_id = int(candidate["payload"].get("doc_id") or 0)
        if per_doc.get(doc_id, 0) >= config.RAG_MAX_CHUNKS_PER_DOC:
            continue
        candidate["score"] = float(score)
        candidate["payload"]["retrieval_tool"] = "exact_document_search"
        output.append(candidate)
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        if len(output) >= top_k:
            break
    return output


def hybrid_search(
    query: str,
    top_k: int = 8,
    rerank_pool: int = 30,
    loai_vb: str | None = None,
    huong: str | None = None,
    collection_name: str | None = None,
    min_score: float | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    co_quan_ban_hanh: str | None = None,
):
    vector = embedder.encode_one(query)
    active_collection = _collection(collection_name)
    hits = store.hybrid_query(
        vector["dense"], vector["sparse"], top_k=rerank_pool,
        flt=_filter(loai_vb, huong), collection_name=active_collection,
    )
    if not hits:
        return []
    passages = [str((hit.payload or {}).get("text") or "") for hit in hits]
    scores = embedder.rerank(query, passages)
    threshold = config.RAG_MIN_RERANK_SCORE if min_score is None else min_score
    ranked = sorted(zip(hits, scores), key=lambda pair: pair[1], reverse=True)
    output: list[dict[str, Any]] = []
    per_doc: dict[int, int] = {}
    for hit, score in ranked:
        if float(score) < threshold:
            continue
        item = _item(hit, float(score))
        if not _passes_metadata_filters(item["payload"], date_from, date_to, co_quan_ban_hanh):
            continue
        doc_id = int(item["payload"].get("doc_id") or 0)
        if per_doc.get(doc_id, 0) >= config.RAG_MAX_CHUNKS_PER_DOC:
            continue
        item["payload"]["retrieval_tool"] = "hybrid_search"
        output.append(item)
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        if len(output) >= top_k:
            break
    return output


def multi_query_search(
    query: str,
    alternative_queries: list[str] | None = None,
    top_k: int = 8,
    loai_vb: str | None = None,
    huong: str | None = None,
    collection_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    co_quan_ban_hanh: str | None = None,
):
    queries = [query]
    for alternative in alternative_queries or []:
        alternative = alternative.strip()
        if alternative and alternative not in queries:
            queries.append(alternative)
    queries = queries[: config.RAG_MULTI_QUERY_COUNT]
    if len(queries) == 1:
        return hybrid_search(
            query, top_k=top_k, rerank_pool=max(top_k * 4, 32),
            loai_vb=loai_vb, huong=huong, collection_name=collection_name,
            date_from=date_from, date_to=date_to, co_quan_ban_hanh=co_quan_ban_hanh,
        )

    merged: dict[str, dict[str, Any]] = {}
    for candidate_query in queries:
        hits = hybrid_search(
            candidate_query, top_k=max(top_k * 2, 12), rerank_pool=max(top_k * 4, 30),
            loai_vb=loai_vb, huong=huong, collection_name=collection_name,
            min_score=0.0, date_from=date_from, date_to=date_to,
            co_quan_ban_hanh=co_quan_ban_hanh,
        )
        for rank, hit in enumerate(hits, start=1):
            fingerprint = hit.get("id") or hashlib.sha1(hit["text"].encode("utf-8", errors="ignore")).hexdigest()
            fused = float(hit["score"]) + 1.0 / (60 + rank)
            if fingerprint not in merged or fused > merged[fingerprint]["fused_score"]:
                merged[fingerprint] = {**hit, "fused_score": fused}
    if not merged:
        return []

    pool = sorted(merged.values(), key=lambda item: item["fused_score"], reverse=True)[: max(30, top_k * 4)]
    scores = embedder.rerank(query, [item["text"] for item in pool])
    ranked = sorted(zip(pool, scores), key=lambda pair: pair[1], reverse=True)
    output: list[dict[str, Any]] = []
    per_doc: dict[int, int] = {}
    for item, score in ranked:
        if float(score) < config.RAG_MIN_RERANK_SCORE:
            continue
        doc_id = int(item["payload"].get("doc_id") or 0)
        if per_doc.get(doc_id, 0) >= config.RAG_MAX_CHUNKS_PER_DOC:
            continue
        item.pop("fused_score", None)
        item["score"] = float(score)
        item["payload"]["retrieval_tool"] = "multi_query_search"
        output.append(item)
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        if len(output) >= top_k:
            break
    return output


def retrieve_neighbors(items: list[dict[str, Any]], window: int = 1, collection_name: str | None = None):
    active_collection = _collection(collection_name)
    output = list(items)
    seen = {(item["payload"].get("doc_id"), item["payload"].get("chunk_index")) for item in output}
    for item in items:
        payload = item["payload"]
        doc_id = payload.get("doc_id")
        chunk_index = payload.get("chunk_index")
        if doc_id is None or chunk_index is None:
            continue
        for point in store.get_chunks_for_doc(int(doc_id), active_collection):
            candidate = _item(point, max(0.0, float(item["score"]) - 0.05))
            index = candidate["payload"].get("chunk_index")
            key = (doc_id, index)
            if key in seen or index is None or abs(int(index) - int(chunk_index)) > window:
                continue
            candidate["payload"]["retrieval_tool"] = "retrieve_neighbors"
            output.append(candidate)
            seen.add(key)
    return output


def retrieve_parent_section(items: list[dict[str, Any]]):
    output = list(items)
    seen_parent_ids: set[str] = set()
    for item in items:
        payload = item["payload"]
        parent_id = str(payload.get("parent_chunk_id") or "")
        parent_text = str(payload.get("parent_text") or "")
        if not parent_id or not parent_text or parent_id in seen_parent_ids:
            continue
        parent_payload = dict(payload)
        parent_payload.update({"text": parent_text, "chunk_kind": "parent", "retrieval_tool": "retrieve_parent_section"})
        output.append({
            "id": f"parent:{parent_id}",
            "text": parent_text,
            "payload": parent_payload,
            "score": max(0.0, float(item["score"]) - 0.03),
        })
        seen_parent_ids.add(parent_id)
    return output


def find_document_relations(query: str):
    relations: list[dict[str, Any]] = []
    for ref in extract_document_refs(query):
        for document in store.search_documents_by_reference(ref, limit=3):
            relations.extend(store.get_document_relations(int(document["id"])))
    return relations
