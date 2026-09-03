"""Lớp lưu trữ: Qdrant (chunk + dense + sparse) và Postgres (doc-level).
Qdrant collection dùng NAMED vectors để hybrid search (Query API + RRF).
"""
import psycopg2
import psycopg2.extras
from qdrant_client import QdrantClient
from qdrant_client import models as qm
from src.core import config

# ----------------------------- Qdrant --------------------------------
_q = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT,
                  api_key=config.QDRANT_API_KEY, https=False, timeout=300.0)
_q_stats = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT,
                        api_key=config.QDRANT_API_KEY, https=False, timeout=3.0)

DENSE_DIM = 1024  # bge-m3


def collection_exists(collection_name: str) -> bool:
    return collection_name in {item.name for item in _q.get_collections().collections}


def ensure_collection(collection_name: str | None = None):
    collection_name = collection_name or config.QDRANT_COLLECTION
    if collection_exists(collection_name):
        return
    _q.create_collection(
        collection_name=collection_name,
        vectors_config={"dense": qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE)},
        sparse_vectors_config={"sparse": qm.SparseVectorParams(index=qm.SparseIndexParams())},
    )
    indexes = {
        "doc_id": qm.PayloadSchemaType.INTEGER,
        "chunk_index": qm.PayloadSchemaType.INTEGER,
        "loai_vb": qm.PayloadSchemaType.KEYWORD,
        "huong": qm.PayloadSchemaType.KEYWORD,
        "parent_chunk_id": qm.PayloadSchemaType.KEYWORD,
    }
    for field, schema in indexes.items():
        _q.create_payload_index(collection_name, field, schema)


def upsert_chunks(points: list[qm.PointStruct], collection_name: str | None = None):
    collection_name = collection_name or config.QDRANT_COLLECTION
    for i in range(0, len(points), 100):
        _q.upsert(collection_name, points=points[i:i + 100])


def hybrid_query(dense, sparse: dict, top_k: int = 20,
                 flt: qm.Filter | None = None,
                 collection_name: str | None = None):
    """Prefetch dense + sparse, hợp nhất bằng RRF (Reciprocal Rank Fusion)."""
    collection_name = collection_name or config.QDRANT_COLLECTION
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
    )
    return res.points


# ---------------------------- Postgres -------------------------------
def pg():
    return psycopg2.connect(config.PG_DSN)


def insert_document(meta: dict) -> int:
    cols = ("so_ky_hieu", "normalized_so_ky_hieu", "ngay_ban_hanh", "loai_vb",
            "viet_tat_loai", "huong", "co_quan_ban_hanh", "nguoi_ky",
            "chuc_vu_nguoi_ky", "trich_yeu", "file_name", "file_path",
            "chu_truong", "linh_vuc", "chuyen_de", "full_text", "source_url",
            "sha256", "extract_method", "extract_confidence", "n_chunks", "raw_meta",
            "tinh_trang_hieu_luc", "hieu_luc_tu", "hieu_luc_den")
    vals = [meta.get(c) for c in cols]
    with pg() as c, c.cursor() as cur:
        cur.execute(
            f"INSERT INTO documents ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))}) "
            "ON CONFLICT (sha256) DO UPDATE SET "
            "file_path = EXCLUDED.file_path, raw_meta = EXCLUDED.raw_meta "
            "RETURNING id",
            vals,
        )
        row = cur.fetchone()
        return row[0] if row else -1


def get_documents(where_sql: str = "", params: tuple = (), limit: int = 500):
    sql = ("SELECT id, so_ky_hieu, ngay_ban_hanh, loai_vb, co_quan_ban_hanh, "
           "trich_yeu, full_text FROM documents")
    if where_sql:
        sql += " WHERE " + where_sql
    sql += " ORDER BY ngay_ban_hanh DESC NULLS LAST LIMIT %s"
    with pg() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params + (limit,))
        return cur.fetchall()


def find_doc_by_soky(so_ky_hieu: str):
    """Fuzzy match số ký hiệu (cho kiểm tra căn cứ)."""
    with pg() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, so_ky_hieu, ngay_ban_hanh, trich_yeu "
            "FROM documents WHERE similarity(so_ky_hieu, %s) > 0.4 "
            "ORDER BY similarity(so_ky_hieu, %s) DESC LIMIT 3",
            (so_ky_hieu, so_ky_hieu))
        return cur.fetchall()

def get_system_stats() -> dict:
    """Lấy thống kê tổng quan từ Postgres và Qdrant cho trang Admin."""
    stats = {
        "total_docs": 0, "total_vectors": 0,
        "by_loai": [], "by_huong": [], "tags": []
    }
    
    # 1. Thống kê từ Postgres
    try:
        with pg() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            # Tổng số văn bản
            cur.execute("SELECT COUNT(*) FROM documents")
            stats["total_docs"] = cur.fetchone()[0]
            
            # Phân loại theo loại văn bản
            cur.execute("SELECT loai_vb, COUNT(*) as cnt FROM documents WHERE loai_vb IS NOT NULL GROUP BY loai_vb ORDER BY cnt DESC")
            stats["by_loai"] = [dict(r) for r in cur.fetchall()]
            
            # Phân loại theo hướng (Đến / Đi)
            cur.execute("SELECT huong, COUNT(*) as cnt FROM documents WHERE huong IS NOT NULL GROUP BY huong")
            stats["by_huong"] = [dict(r) for r in cur.fetchall()]
            
            # Đếm các Tag chuyên đề phổ biến
            cur.execute("""
                SELECT unnest(chuyen_de) as tag, COUNT(*) as cnt 
                FROM documents 
                GROUP BY tag ORDER BY cnt DESC LIMIT 10
            """)
            stats["tags"] = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"Lỗi đọc DB Postgres: {e}")

    # 2. Thống kê từ Qdrant
    try:
        collection_info = _q_stats.get_collection(config.QDRANT_COLLECTION)
        stats["total_vectors"] = collection_info.points_count
    except Exception as e:
        print(f"Lỗi đọc Qdrant: {e}")

    return stats

def get_chunks_for_doc(doc_id: int, collection_name: str | None = None):
    collection_name = collection_name or config.QDRANT_COLLECTION
    if not collection_exists(collection_name):
        return []
    records = []
    offset = None
    while True:
        batch, offset = _q.scroll(
            collection_name=collection_name,
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))
            ]),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        records.extend(batch)
        if offset is None:
            break
    return sorted(records, key=lambda point: int((point.payload or {}).get("chunk_index", 999999)))


def search_documents_by_reference(reference: str, limit: int = 5):
    normalized = reference.replace("đ", "d").replace("Đ", "D").replace(" ", "").upper()
    fields = ("id, so_ky_hieu, normalized_so_ky_hieu, ngay_ban_hanh, loai_vb, "
              "huong, co_quan_ban_hanh, trich_yeu, full_text, source_url, "
              "tinh_trang_hieu_luc, hieu_luc_tu, hieu_luc_den")
    with pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        try:
            cursor.execute(
                f"SELECT {fields} FROM documents "
                "WHERE normalized_so_ky_hieu = %s OR similarity(normalized_so_ky_hieu, %s) > 0.45 "
                "ORDER BY (normalized_so_ky_hieu = %s) DESC, "
                "similarity(normalized_so_ky_hieu, %s) DESC LIMIT %s",
                (normalized, normalized, normalized, normalized, limit),
            )
        except psycopg2.errors.UndefinedColumn:
            connection.rollback()
            cursor.execute(
                "SELECT id, so_ky_hieu, ngay_ban_hanh, loai_vb, huong, "
                "co_quan_ban_hanh, trich_yeu, full_text, source_url "
                "FROM documents WHERE similarity(so_ky_hieu, %s) > 0.4 "
                "ORDER BY similarity(so_ky_hieu, %s) DESC LIMIT %s",
                (reference, reference, limit),
            )
        return cursor.fetchall()


def get_document_relations(document_id: int):
    try:
        with pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                """SELECT r.*, target.so_ky_hieu AS target_so_ky_hieu,
                          target.ngay_ban_hanh AS target_ngay_ban_hanh
                   FROM document_relations r
                   LEFT JOIN documents target ON target.id = r.target_document_id
                   WHERE r.source_document_id = %s OR r.target_document_id = %s
                   ORDER BY r.verified DESC, r.confidence DESC NULLS LAST""",
                (document_id, document_id),
            )
            return [dict(row) for row in cursor.fetchall()]
    except psycopg2.errors.UndefinedTable:
        return []


def log_rag_query(data: dict):
    try:
        with pg() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO rag_query_logs
                   (query_text, intent, plan, source_doc_ids, rerank_scores, attempts,
                    confidence, latency_ms, answer_status, error_text)
                   VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    data.get("query_text"), data.get("intent"),
                    psycopg2.extras.Json(data.get("plan") or {}),
                    data.get("source_doc_ids") or [], data.get("rerank_scores") or [],
                    data.get("attempts", 1), data.get("confidence"), data.get("latency_ms"),
                    data.get("answer_status"), data.get("error_text"),
                ),
            )
    except Exception as exc:
        print(f"[rag-log] Không ghi được query log: {exc}")