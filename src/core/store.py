"""Lớp lưu trữ: Qdrant (chunk + dense + sparse) và Postgres (văn bản, tệp, quan hệ).
Qdrant collection dùng NAMED vectors để hybrid search (Query API + RRF).
"""
import logging
import math
import time
from contextlib import contextmanager
from threading import Lock

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from src.core import config
from src.core.runtime import remaining

logger = logging.getLogger(__name__)

# ----------------------------- Qdrant --------------------------------
_q = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT,
                  api_key=config.QDRANT_API_KEY, https=False, timeout=config.QDRANT_TIMEOUT_SECONDS)
_q_stats = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT,
                        api_key=config.QDRANT_API_KEY, https=False, timeout=3.0)

DENSE_DIM = 1024  # bge-m3

_PAYLOAD_INDEXES = {
    "doc_id": qm.PayloadSchemaType.INTEGER,
    "file_id": qm.PayloadSchemaType.INTEGER,
    "chunk_index": qm.PayloadSchemaType.INTEGER,
    "loai_vb": qm.PayloadSchemaType.KEYWORD,
    "huong": qm.PayloadSchemaType.KEYWORD,
    "linh_vuc": qm.PayloadSchemaType.KEYWORD,
    "parent_chunk_id": qm.PayloadSchemaType.KEYWORD,
    "issued_day": qm.PayloadSchemaType.INTEGER,
    "agency_normalized": qm.PayloadSchemaType.KEYWORD,
    "index_version": qm.PayloadSchemaType.KEYWORD,
    "ready": qm.PayloadSchemaType.BOOL,
    "chunk_kind": qm.PayloadSchemaType.KEYWORD,
    "file_role": qm.PayloadSchemaType.KEYWORD,
    "ingest_run": qm.PayloadSchemaType.KEYWORD,
}


def _qdrant_timeout() -> int:
    return max(1, math.ceil(remaining(config.QDRANT_TIMEOUT_SECONDS)))


def _doc_condition(doc_id: int) -> qm.FieldCondition:
    return qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=int(doc_id)))


def collection_exists(collection_name: str) -> bool:
    return collection_name in {item.name for item in _q.get_collections().collections}


def ensure_collection(collection_name: str | None = None):
    collection_name = collection_name or config.RAG_COLLECTION
    if not collection_exists(collection_name):
        _q.create_collection(
            collection_name=collection_name,
            vectors_config={"dense": qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE)},
            sparse_vectors_config={"sparse": qm.SparseVectorParams(index=qm.SparseIndexParams())},
        )
    existing = _q.get_collection(collection_name).payload_schema
    for field, schema in _PAYLOAD_INDEXES.items():
        if field not in existing:
            _q.create_payload_index(collection_name, field, schema, wait=True)


def upsert_chunks(points: list[qm.PointStruct], collection_name: str | None = None):
    collection_name = collection_name or config.RAG_COLLECTION
    for i in range(0, len(points), 100):
        _q.upsert(collection_name, points=points[i:i + 100], wait=True)


def hybrid_query(dense, sparse: dict, top_k: int = 20,
                 flt: qm.Filter | None = None,
                 collection_name: str | None = None):
    """Prefetch dense + sparse, hợp nhất bằng RRF (Reciprocal Rank Fusion)."""
    collection_name = collection_name or config.RAG_COLLECTION
    sparse_vec = qm.SparseVector(indices=list(sparse.keys()),
                                 values=list(sparse.values()))
    res = _q.query_points(
        collection_name=collection_name,
        prefetch=[
            qm.Prefetch(query=dense, using="dense", limit=top_k * 2, filter=flt),
            qm.Prefetch(query=sparse_vec, using="sparse", limit=top_k * 2, filter=flt),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        limit=top_k, with_payload=True,
        timeout=_qdrant_timeout(),
    )
    return res.points


def hybrid_query_groups(dense, sparse: dict, group_by: str, limit: int, group_size: int,
                        flt: qm.Filter | None = None, collection_name: str | None = None):
    """Hybrid (RRF) search returning the best ``group_size`` points for each of up to ``limit`` groups."""
    collection_name = collection_name or config.RAG_COLLECTION
    sparse_vec = qm.SparseVector(indices=list(sparse.keys()), values=list(sparse.values()))
    prefetch_limit = min(max(limit * group_size * 3, 100), 5000)
    res = _q.query_points_groups(
        collection_name=collection_name,
        group_by=group_by,
        prefetch=[
            qm.Prefetch(query=dense, using="dense", limit=prefetch_limit, filter=flt),
            qm.Prefetch(query=sparse_vec, using="sparse", limit=prefetch_limit, filter=flt),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        query_filter=flt,
        limit=limit, group_size=group_size, with_payload=True,
        timeout=_qdrant_timeout(),
    )
    return res.groups


def get_chunks(doc_id: int, file_id: int | None = None, indices: list[int] | None = None,
               collection_name: str | None = None):
    """Chunk của một văn bản (hoặc một tệp), sắp theo thứ tự trong tệp."""
    collection_name = collection_name or config.RAG_COLLECTION
    conditions = [_doc_condition(doc_id), qm.FieldCondition(key="chunk_kind", match=qm.MatchValue(value="child"))]
    if file_id is not None:
        conditions.append(qm.FieldCondition(key="file_id", match=qm.MatchValue(value=int(file_id))))
    if indices is not None:
        if not indices:
            return []
        conditions.append(qm.FieldCondition(key="chunk_index", match=qm.MatchAny(any=indices)))
    records = []
    offset = None
    while True:
        batch, offset = _q.scroll(
            collection_name=collection_name,
            scroll_filter=qm.Filter(must=conditions),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
            timeout=_qdrant_timeout(),
        )
        records.extend(batch)
        if offset is None:
            break
    return sorted(records, key=lambda point: (int((point.payload or {}).get("file_id") or 0),
                                              int((point.payload or {}).get("chunk_index", 999999))))


def delete_document_chunks(doc_id, collection_name=None):
    _q.delete(collection_name or config.RAG_COLLECTION,
              points_selector=qm.FilterSelector(filter=qm.Filter(must=[_doc_condition(doc_id)])), wait=True)


def delete_stale_document_chunks(doc_id, keep_run: str, collection_name=None):
    """Remove points of earlier ingest runs after the new run has been written."""
    _q.delete(collection_name or config.RAG_COLLECTION, points_selector=qm.FilterSelector(filter=qm.Filter(
        must=[_doc_condition(doc_id)],
        must_not=[qm.FieldCondition(key="ingest_run", match=qm.MatchValue(value=keep_run))],
    )), wait=True)


def publish_document_chunks(doc_id, collection_name=None):
    _q.set_payload(collection_name or config.RAG_COLLECTION, payload={"ready": True},
                   points=qm.Filter(must=[_doc_condition(doc_id)]), wait=True)


# ---------------------------- Postgres -------------------------------
_pool = None
_pool_lock = Lock()


@contextmanager
def pg():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(1, config.PG_POOL_SIZE, config.PG_DSN, connect_timeout=5)
    connection = _pool.getconn()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", (str(max(1, int(remaining(10) * 1000))),))
            yield connection
    finally:
        _pool.putconn(connection, close=bool(connection.closed))


def _dict_cursor(connection):
    return connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


_READY_SQL = "d.ingest_status = 'ready' AND d.index_version = %s"

DOCUMENT_COLUMNS = ("so_ky_hieu", "normalized_so_ky_hieu", "ngay_ban_hanh", "loai_vb",
                    "viet_tat_loai", "huong", "co_quan_ban_hanh", "nguoi_ky",
                    "chuc_vu_nguoi_ky", "trich_yeu", "file_name", "file_path",
                    "chu_truong", "linh_vuc", "chuyen_de", "vai_tro_van_ban", "full_text",
                    "source_url", "sha256", "extract_method", "extract_confidence", "n_chunks",
                    "n_files", "raw_meta", "tinh_trang_hieu_luc", "hieu_luc_tu", "hieu_luc_den")


def upsert_document(source_key: str, meta: dict) -> int:
    """Insert or update the văn bản identified by source_key; returns its id."""
    cols = [column for column in DOCUMENT_COLUMNS if column in meta]
    values = [psycopg2.extras.Json(meta[c]) if c == "raw_meta" and isinstance(meta[c], (dict, list)) else meta[c]
              for c in cols]
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in cols)
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO documents (source_key, {', '.join(cols)}) "
            f"VALUES (%s, {', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (source_key) DO UPDATE SET {updates + ', ' if updates else ''}updated_at = now() "
            "RETURNING id",
            [source_key, *values],
        )
        return int(cursor.fetchone()[0])


def get_document(doc_id: int) -> dict | None:
    with pg() as connection, _dict_cursor(connection) as cursor:
        cursor.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def document_by_source_key(source_key: str) -> dict | None:
    with pg() as connection, _dict_cursor(connection) as cursor:
        cursor.execute("SELECT id, ingest_status, index_version FROM documents WHERE source_key = %s", (source_key,))
        row = cursor.fetchone()
        return dict(row) if row else None


FILE_COLUMNS = ("file_index", "file_name", "file_path", "role", "duplicate_of", "extract_method",
                "full_text", "n_chunks", "ingest_status", "index_version", "ingest_error")


def upsert_file(document_id: int, sha256: str, fields: dict) -> int:
    cols = [column for column in FILE_COLUMNS if column in fields]
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in cols)
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO document_files (document_id, sha256, {', '.join(cols)}) "
            f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (document_id, sha256) DO UPDATE SET {updates}, updated_at = now() RETURNING id",
            [document_id, sha256, *[fields[c] for c in cols]],
        )
        return int(cursor.fetchone()[0])


def update_file(file_id: int, **fields):
    cols = [column for column in FILE_COLUMNS if column in fields]
    if not cols:
        return
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE document_files SET {', '.join(f'{c} = %s' for c in cols)}, updated_at = now() WHERE id = %s",
            [*[fields[c] for c in cols], file_id],
        )


def document_files(document_id: int, include_text: bool = False) -> list[dict]:
    text_column = ", full_text" if include_text else ""
    with pg() as connection, _dict_cursor(connection) as cursor:
        cursor.execute(
            "SELECT id, document_id, file_index, file_name, file_path, sha256, role, duplicate_of, "
            f"extract_method, n_chunks, ingest_status, index_version, ingest_error{text_column} "
            "FROM document_files WHERE document_id = %s ORDER BY file_index, id",
            (document_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def ready_file_hashes(document_id: int) -> set[str]:
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT sha256 FROM document_files WHERE document_id = %s AND index_version = %s "
            "AND ingest_status IN ('ready', 'duplicate')",
            (document_id, config.INGEST_VERSION),
        )
        return {row[0] for row in cursor.fetchall()}


def delete_files_except(document_id: int, keep_ids: list[int]):
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM document_files WHERE document_id = %s AND NOT (id = ANY(%s))",
                       (document_id, list(keep_ids) or [-1]))


def set_ingest_status(doc_id, status, error=None):
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE documents SET ingest_status=%s, index_version=%s, ingest_error=%s, updated_at=now() "
                       "WHERE id=%s", (status, config.INGEST_VERSION, error, doc_id))


# --- filters -----------------------------------------------------------

_agency_cache: dict = {"at": 0.0, "rows": []}
_agency_lock = Lock()


def _agency_rows() -> list[tuple[str, str]]:
    from src.services.document_fields import normalize_agency
    with _agency_lock:
        if time.monotonic() - _agency_cache["at"] < 60:
            return _agency_cache["rows"]
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT co_quan_ban_hanh FROM documents d "
                       f"WHERE {_READY_SQL} AND co_quan_ban_hanh IS NOT NULL", (config.INGEST_VERSION,))
        rows = [(row[0], normalize_agency(row[0])) for row in cursor.fetchall()]
    with _agency_lock:
        _agency_cache.update(at=time.monotonic(), rows=rows)
    return rows


def matching_agencies(expected_normalized: str) -> list[str]:
    """Normalized agency names containing the expected (normalized) text."""
    return sorted({normalized for _, normalized in _agency_rows() if expected_normalized in normalized})


def matching_agency_names(expected_normalized: str) -> list[str]:
    return sorted({raw for raw, normalized in _agency_rows() if expected_normalized in normalized})


def document_filter_sql(filters: dict, alias: str = "d") -> tuple[str, list]:
    """SQL WHERE (ready văn bản only) for normalized filters from document_fields.normalize_filters."""
    from src.services.document_fields import normalize_agency
    conditions = [f"{alias}.ingest_status = 'ready'", f"{alias}.index_version = %s"]
    params: list = [config.INGEST_VERSION]
    if filters.get("loai_vb"):
        conditions.append(f"{alias}.loai_vb = ANY(%s)")
        params.append(list(filters["loai_vb"]))
    if filters.get("huong"):
        conditions.append(f"{alias}.huong = ANY(%s)")
        params.append(list(filters["huong"]))
    if filters.get("linh_vuc"):
        conditions.append(f"{alias}.linh_vuc && %s::text[]")
        params.append(list(filters["linh_vuc"]))
    if filters.get("date_from"):
        conditions.append(f"{alias}.ngay_ban_hanh >= %s")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        conditions.append(f"{alias}.ngay_ban_hanh <= %s")
        params.append(filters["date_to"])
    if filters.get("co_quan_ban_hanh"):
        conditions.append(f"{alias}.co_quan_ban_hanh = ANY(%s)")
        params.append(matching_agency_names(normalize_agency(filters["co_quan_ban_hanh"])) or ["__no_agency__"])
    return " AND ".join(conditions), params


def iter_documents(filters: dict, limit: int, batch_size: int = 200):
    """Keyset-paginated ready documents matching filters (no full_text)."""
    where, params = document_filter_sql(filters)
    after, returned = 0, 0
    while returned < limit:
        size = min(batch_size, limit - returned)
        with pg() as connection, _dict_cursor(connection) as cursor:
            cursor.execute(
                "SELECT d.id, d.so_ky_hieu, d.ngay_ban_hanh, d.loai_vb, d.huong, d.co_quan_ban_hanh, "
                f"d.trich_yeu, d.source_url FROM documents d WHERE {where} AND d.id > %s ORDER BY d.id LIMIT %s",
                [*params, after, size],
            )
            rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            return
        yield from rows
        returned += len(rows)
        after = rows[-1]["id"]


def count_documents(filters: dict) -> int:
    where, params = document_filter_sql(filters)
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM documents d WHERE {where}", params)
        return int(cursor.fetchone()[0])


# --- lookups -----------------------------------------------------------

_LOOKUP_FIELDS = ("d.id, d.so_ky_hieu, d.normalized_so_ky_hieu, d.ngay_ban_hanh, d.loai_vb, d.huong, "
                  "d.co_quan_ban_hanh, d.trich_yeu, d.source_url, d.tinh_trang_hieu_luc, d.hieu_luc_tu, "
                  "d.hieu_luc_den, d.n_files")


def find_doc_by_soky(so_ky_hieu: str):
    """Fuzzy match số ký hiệu (cho kiểm tra căn cứ), chỉ trên văn bản đã sẵn sàng."""
    with pg() as connection, _dict_cursor(connection) as cursor:
        cursor.execute(
            "SELECT d.id, d.so_ky_hieu, d.ngay_ban_hanh, d.trich_yeu FROM documents d "
            f"WHERE {_READY_SQL} AND similarity(d.so_ky_hieu, %s) > 0.4 "
            "ORDER BY similarity(d.so_ky_hieu, %s) DESC LIMIT 3",
            (config.INGEST_VERSION, so_ky_hieu, so_ky_hieu))
        return cursor.fetchall()


def search_documents_by_reference(reference: str, limit: int = 5):
    from src.services.document_fields import normalize_document_ref
    with pg() as connection, _dict_cursor(connection) as cursor:
        cursor.execute(
            f"SELECT {_LOOKUP_FIELDS} FROM documents d "
            f"WHERE d.normalized_so_ky_hieu = %s AND {_READY_SQL} "
            "ORDER BY d.ngay_ban_hanh DESC NULLS LAST LIMIT %s",
            (normalize_document_ref(reference), config.INGEST_VERSION, limit),
        )
        return cursor.fetchall()


def search_documents_by_number(number: str, limit: int = 5):
    """Văn bản có số (phần trước dấu '/') khớp, ví dụ '2072' -> 2072/SKHCN-QLCN."""
    with pg() as connection, _dict_cursor(connection) as cursor:
        cursor.execute(
            f"SELECT {_LOOKUP_FIELDS} FROM documents d "
            f"WHERE split_part(d.normalized_so_ky_hieu, '/', 1) = %s AND {_READY_SQL} "
            "ORDER BY d.ngay_ban_hanh DESC NULLS LAST LIMIT %s",
            (str(int(number)), config.INGEST_VERSION, limit),
        )
        return cursor.fetchall()


def documents_by_ids(doc_ids) -> dict[int, dict]:
    ids = sorted({int(doc_id) for doc_id in doc_ids if doc_id is not None})
    if not ids:
        return {}
    with pg() as connection, _dict_cursor(connection) as cursor:
        cursor.execute(f"SELECT {_LOOKUP_FIELDS} FROM documents d WHERE d.id = ANY(%s)", (ids,))
        return {int(row["id"]): dict(row) for row in cursor.fetchall()}


def resolve_reference(normalized_ref: str) -> int | None:
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM documents WHERE normalized_so_ky_hieu = %s "
                       "ORDER BY ngay_ban_hanh DESC NULLS LAST LIMIT 1", (normalized_ref,))
        row = cursor.fetchone()
        return int(row[0]) if row else None


def replace_document_relations(document_id: int, relations: list[dict]):
    """Replace unverified extracted relations of a document; verified ones are kept."""
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM document_relations WHERE source_document_id = %s AND NOT verified", (document_id,))
        for relation in relations:
            cursor.execute(
                """INSERT INTO document_relations
                   (source_document_id, target_document_id, target_ref_text, normalized_target_ref,
                    relation_type, evidence_text, confidence, verified)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
                   ON CONFLICT (source_document_id, relation_type, target_ref_text) DO NOTHING""",
                (document_id, relation.get("target_document_id"), relation["target_ref_text"],
                 relation["normalized_target_ref"], relation["relation_type"],
                 relation.get("evidence_text"), relation.get("confidence")),
            )
        # Relations from other documents that point at this document's reference can now resolve.
        cursor.execute(
            """UPDATE document_relations r SET target_document_id = d.id
               FROM documents d
               WHERE d.id = %s AND r.target_document_id IS NULL
                 AND r.normalized_target_ref = d.normalized_so_ky_hieu""",
            (document_id,),
        )


def get_document_relations(document_ids) -> list[dict]:
    ids = sorted({int(doc_id) for doc_id in document_ids if doc_id is not None})
    if not ids:
        return []
    with pg() as connection, _dict_cursor(connection) as cursor:
        cursor.execute(
            """SELECT r.id, r.source_document_id, src.so_ky_hieu AS source_so_ky_hieu,
                      r.target_document_id, r.target_ref_text, r.relation_type, r.evidence_text,
                      r.confidence, r.verified, target.so_ky_hieu AS target_so_ky_hieu,
                      target.ngay_ban_hanh AS target_ngay_ban_hanh
               FROM document_relations r
               JOIN documents src ON src.id = r.source_document_id
               LEFT JOIN documents target ON target.id = r.target_document_id
               WHERE r.source_document_id = ANY(%s) OR r.target_document_id = ANY(%s)
               ORDER BY r.verified DESC, r.confidence DESC NULLS LAST
               LIMIT 60""",
            (ids, ids),
        )
        return [dict(row) for row in cursor.fetchall()]


def log_rag_query(data: dict):
    try:
        with pg() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO rag_query_logs
                   (query_text, intent, plan, source_doc_ids, rerank_scores, attempts,
                    confidence, latency_ms, answer_status, error_text, user_id, model)
                   VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    data.get("query_text"), data.get("intent"),
                    psycopg2.extras.Json(data.get("plan") or {}),
                    data.get("source_doc_ids") or [], data.get("rerank_scores") or [],
                    data.get("attempts", 1), data.get("confidence"), data.get("latency_ms"),
                    data.get("answer_status"), data.get("error_text"),
                    data.get("user_id"), data.get("model"),
                ),
            )
    except Exception as exc:
        logger.warning("Không ghi được query log: %s", exc)


def ready_doc_ids(ids):
    ids = [int(value) for value in ids if value is not None]
    if not ids:
        return set()
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT d.id FROM documents d WHERE d.id = ANY(%s) AND {_READY_SQL}",
                       (ids, config.INGEST_VERSION))
        return {row[0] for row in cursor.fetchall()}


def has_ready_documents():
    with pg() as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT 1 FROM documents d WHERE {_READY_SQL} LIMIT 1", (config.INGEST_VERSION,))
        return cursor.fetchone() is not None


def get_system_stats() -> dict:
    """Thống kê cho trang Admin: chỉ đếm văn bản sẵn sàng, kèm phân bố trạng thái."""
    stats = {
        "total_docs": 0, "total_files": 0, "total_vectors": 0,
        "by_loai": [], "by_huong": [], "tags": [], "by_status": [], "file_roles": [],
        "index_version": config.INGEST_VERSION, "collection": config.RAG_COLLECTION,
    }
    try:
        with pg() as connection, _dict_cursor(connection) as cursor:
            version = (config.INGEST_VERSION,)
            cursor.execute(f"SELECT count(*) AS n FROM documents d WHERE {_READY_SQL}", version)
            stats["total_docs"] = cursor.fetchone()["n"]
            cursor.execute(f"SELECT count(*) AS n FROM document_files f JOIN documents d ON d.id = f.document_id "
                           f"WHERE {_READY_SQL}", version)
            stats["total_files"] = cursor.fetchone()["n"]
            cursor.execute(f"SELECT d.loai_vb, count(*) AS cnt FROM documents d WHERE {_READY_SQL} "
                           "AND d.loai_vb IS NOT NULL GROUP BY d.loai_vb ORDER BY cnt DESC", version)
            stats["by_loai"] = [dict(r) for r in cursor.fetchall()]
            cursor.execute(f"SELECT d.huong, count(*) AS cnt FROM documents d WHERE {_READY_SQL} "
                           "AND d.huong IS NOT NULL GROUP BY d.huong", version)
            stats["by_huong"] = [dict(r) for r in cursor.fetchall()]
            cursor.execute(f"SELECT unnest(d.chuyen_de) AS tag, count(*) AS cnt FROM documents d "
                           f"WHERE {_READY_SQL} GROUP BY tag ORDER BY cnt DESC LIMIT 10", version)
            stats["tags"] = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT ingest_status AS status, COALESCE(index_version, '') AS index_version, "
                           "count(*) AS cnt FROM documents GROUP BY 1, 2 ORDER BY cnt DESC")
            stats["by_status"] = [dict(r) for r in cursor.fetchall()]
            cursor.execute(f"SELECT f.role, count(*) AS cnt FROM document_files f JOIN documents d "
                           f"ON d.id = f.document_id WHERE {_READY_SQL} GROUP BY f.role", version)
            stats["file_roles"] = [dict(r) for r in cursor.fetchall()]
    except Exception as exc:
        logger.warning("Lỗi đọc thống kê Postgres: %s", exc)

    try:
        stats["total_vectors"] = _q_stats.get_collection(config.RAG_COLLECTION).points_count
    except Exception as exc:
        logger.warning("Lỗi đọc thống kê Qdrant: %s", exc)
    return stats
