"""Lớp lưu trữ: Qdrant (chunk + dense + sparse) và Postgres (doc-level).
Qdrant collection dùng NAMED vectors để hybrid search (Query API + RRF).
"""
import psycopg2
import psycopg2.extras
from qdrant_client import QdrantClient
from qdrant_client import models as qm
from . import config

# ----------------------------- Qdrant --------------------------------
_q = QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT,
                  api_key=config.QDRANT_API_KEY, https=False, timeout=60.0)

DENSE_DIM = 1024  # bge-m3


def ensure_collection():
    names = [c.name for c in _q.get_collections().collections]
    if config.QDRANT_COLLECTION in names:
        return
    _q.create_collection(
        collection_name=config.QDRANT_COLLECTION,
        vectors_config={"dense": qm.VectorParams(size=DENSE_DIM,
                                                 distance=qm.Distance.COSINE)},
        sparse_vectors_config={"sparse": qm.SparseVectorParams(
            index=qm.SparseIndexParams())},
    )
    # index payload để lọc nhanh khi tổng hợp
    for field in ("doc_id", "loai_vb", "huong"):
        _q.create_payload_index(config.QDRANT_COLLECTION, field,
                                qm.PayloadSchemaType.KEYWORD)


def upsert_chunks(points: list[qm.PointStruct]):
    for i in range(0, len(points), 100):
        _q.upsert(config.QDRANT_COLLECTION, points=points[i:i + 100])


def hybrid_query(dense, sparse: dict, top_k: int = 20,
                 flt: qm.Filter | None = None):
    """Prefetch dense + sparse, hợp nhất bằng RRF (Reciprocal Rank Fusion)."""
    sparse_vec = qm.SparseVector(indices=list(sparse.keys()),
                                 values=list(sparse.values()))
    res = _q.query_points(
        collection_name=config.QDRANT_COLLECTION,
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
    cols = ("so_ky_hieu", "ngay_ban_hanh", "loai_vb", "viet_tat_loai", "huong",
            "co_quan_ban_hanh",
            "nguoi_ky", "chuc_vu_nguoi_ky", "trich_yeu", "file_name", "file_path",
            "chu_truong", "linh_vuc", "chuyen_de"
            "full_text", "source_url", "sha256", "extract_method", "n_chunks", "raw_meta")
    vals = [meta.get(c) for c in cols]
    with pg() as c, c.cursor() as cur:
        cur.execute(
            f"INSERT INTO documents ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))}) "
            "ON CONFLICT (sha256) DO NOTHING RETURNING id",
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
