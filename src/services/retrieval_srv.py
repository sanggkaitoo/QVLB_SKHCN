from __future__ import annotations

import hashlib
import re
from typing import Any

from qdrant_client import models as qm

from src.core import config, embedder, store
from src.services.chunking import normalize_document_ref
from src.services.document_fields import normalize_agency, issued_day


DOCUMENT_REF_RE = re.compile(
    r"(?<!\w)(\d{1,6}\s*/\s*[0-9A-ZÀ-ỸĐ][0-9A-ZÀ-ỸĐ./\-]*(?:\s*-\s*[0-9A-ZÀ-ỸĐ./\-]+)*)(?!\w)",
    re.IGNORECASE,
)


def extract_document_refs(query: str) -> list[str]:
    refs: list[str] = []
    for match in DOCUMENT_REF_RE.finditer(query or ""):
        value = normalize_document_ref(match.group(1).rstrip(".,;"))
        if value and value not in refs:
            refs.append(value)
    return refs


def _collection(collection_name: str | None = None) -> str:
    requested = collection_name or config.RAG_COLLECTION
    return requested


def _filter(loai_vb=None, huong=None, doc_ids=None, date_from=None, date_to=None, co_quan_ban_hanh=None):
    conditions = [
        qm.FieldCondition(key="index_version", match=qm.MatchValue(value=config.INGEST_VERSION)),
        qm.FieldCondition(key="ready", match=qm.MatchValue(value=True)),
    ]
    if loai_vb:
        conditions.append(qm.FieldCondition(key="loai_vb", match=qm.MatchValue(value=loai_vb)))
    if huong:
        conditions.append(qm.FieldCondition(key="huong", match=qm.MatchValue(value=huong)))
    if doc_ids is not None:
        conditions.append(qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=doc_ids or [-1])))
    if date_from or date_to:
        lower, upper = issued_day(date_from), issued_day(date_to)
        if (date_from and lower is None) or (date_to and upper is None):
            raise ValueError("Invalid date filter")
        conditions.append(qm.FieldCondition(key="issued_day", range=qm.Range(gte=lower, lte=upper)))
    if co_quan_ban_hanh:
        names = store.matching_agencies(normalize_agency(co_quan_ban_hanh))
        conditions.append(qm.FieldCondition(key="agency_normalized", match=qm.MatchAny(any=names or ["__no_agency__"])))
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
        expected = normalize_agency(co_quan_ban_hanh)
        actual = normalize_agency(payload.get("co_quan_ban_hanh"))
        if not actual or expected not in actual:
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


def exact_document_search(query: str, top_k: int = 8, collection_name: str | None = None, **filters):
    refs = extract_document_refs(query)
    if not refs:
        return []
    documents: list[dict[str, Any]] = []
    seen_docs: set[int] = set()
    for ref in refs:
        for document in store.search_documents_by_reference(ref, limit=5):
            if not _passes_metadata_filters(document, filters.get("date_from"), filters.get("date_to"), filters.get("co_quan_ban_hanh")):
                continue
            if any(filters.get(key) and filters[key] != document.get(key) for key in ("loai_vb", "huong")):
                continue
            doc_id = int(document["id"])
            if doc_id not in seen_docs:
                documents.append(dict(document))
                seen_docs.add(doc_id)

    candidates: list[dict[str, Any]] = []
    active_collection = _collection(collection_name)
    if not documents:
        return []
    vector = embedder.encode_one(query)
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
        chunks = store.hybrid_query(vector["dense"], vector["sparse"], top_k=12,
                                    flt=_filter(doc_ids=[doc_id], **filters), collection_name=active_collection)
        for chunk in chunks:
            candidate = _item(chunk)
            candidate["payload"] = {**common_metadata, **candidate["payload"]}
            candidates.append(candidate)
    if not candidates:
        return []

    passages = [candidate["text"] for candidate in candidates]
    scores = embedder.rerank(query, passages)
    ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
    output: list[dict[str, Any]] = []
    per_doc: dict[int, int] = {}
    for candidate, score in ranked:
        if float(score) < config.RAG_MIN_RERANK_SCORE:
            continue
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
        flt=_filter(loai_vb, huong, date_from=date_from, date_to=date_to, co_quan_ban_hanh=co_quan_ban_hanh), collection_name=active_collection,
    )
    if not hits:
        return []
    ready_ids = store.ready_doc_ids({(hit.payload or {}).get("doc_id") for hit in hits})
    hits = [hit for hit in hits if (hit.payload or {}).get("doc_id") in ready_ids]
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
    vectors = embedder.encode(queries)
    flt = _filter(loai_vb, huong, date_from=date_from, date_to=date_to, co_quan_ban_hanh=co_quan_ban_hanh)
    for candidate_query, vector in zip(queries, vectors):
        hits = [_item(hit) for hit in store.hybrid_query(
            vector["dense"], vector["sparse"], top_k=max(top_k * 4, 32),
            flt=flt, collection_name=_collection(collection_name))]
        for rank, hit in enumerate(hits, start=1):
            fingerprint = hit.get("id") or hashlib.sha1(hit["text"].encode("utf-8", errors="ignore")).hexdigest()
            if fingerprint not in merged:
                merged[fingerprint] = {**hit, "fused_score": 0.0}
            merged[fingerprint]["fused_score"] += 1.0 / (60 + rank)
    if not merged:
        return []

    pool = sorted(merged.values(), key=lambda item: item["fused_score"], reverse=True)[: max(30, top_k * 4)]
    ready_ids = store.ready_doc_ids({item["payload"].get("doc_id") for item in pool})
    pool = [item for item in pool if item["payload"].get("doc_id") in ready_ids]
    if not pool:
        return []
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
    grouped = {}
    for item in items:
        payload = item["payload"]
        doc_id = payload.get("doc_id")
        chunk_index = payload.get("chunk_index")
        if doc_id is None or chunk_index is None:
            continue
        group = grouped.setdefault(int(doc_id), {"indices": set(), "score": 0.0})
        group["indices"].update(range(max(0, int(chunk_index) - window), int(chunk_index) + window + 1))
        group["score"] = max(group["score"], float(item["score"]))
    for doc_id, group in grouped.items():
        for point in store.get_chunks_for_doc(doc_id, active_collection, indices=sorted(group["indices"])):
            candidate = _item(point, max(0.0, group["score"] - 0.05))
            index = candidate["payload"].get("chunk_index")
            key = (doc_id, index)
            if key in seen or index is None or not candidate["payload"].get("ready"):
                continue
            candidate["payload"]["retrieval_tool"] = "retrieve_neighbors"
            output.append(candidate)
            seen.add(key)
    return output


def retrieve_parent_section(items: list[dict[str, Any]]):
    output = []
    seen_parent_ids: set[str] = set()
    for item in items:
        payload = item["payload"]
        parent_id = str(payload.get("parent_chunk_id") or "")
        parent_text = str(payload.get("parent_text") or "")
        if not parent_id or not parent_text or item["text"] not in parent_text:
            output.append(item)
            continue
        if parent_id in seen_parent_ids:
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
