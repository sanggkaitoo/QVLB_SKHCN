"""Pipeline nạp: extract -> AI metadata -> chunk -> embed(dense+sparse) ->
Qdrant + Postgres. GIỮ bản gốc (copy sang STORE_DIR), KHÔNG xóa.
"""
import os
import json
import uuid
import shutil
import hashlib
from qdrant_client import models as qm
from . import config, extract, metadata, embedder, store


def split_text(text: str, size: int, overlap: int,
               seps=("\nĐiều ", "\nKhoản ", "\n\n", "\n", ". ", " ")) -> list[str]:
    """Splitter đệ quy gọn (tôn trọng cấu trúc văn bản), không cần langchain."""
    if len(text) <= size:
        return [text] if text.strip() else []
    sep = next((s for s in seps if s in text), None)
    if sep is None:                       # cắt cứng
        return [text[i:i + size] for i in range(0, len(text), size - overlap)]
    parts, buf, out = text.split(sep), "", []
    for p in parts:
        piece = (buf + sep + p) if buf else p
        if len(piece) <= size:
            buf = piece
        else:
            if buf:
                out.append(buf)
            buf = (out[-1][-overlap:] + sep + p) if out else p
            if len(buf) > size:           # đoạn con vẫn dài -> đệ quy
                out.extend(split_text(buf, size, overlap, seps[1:]))
                buf = ""
    if buf.strip():
        out.append(buf)
    return [c for c in out if c.strip()]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(8192), b""):
            h.update(blk)
    return h.hexdigest()


def ingest_file(file_path: str, huong: str = "di", raw_meta: dict | None = None,
                source_url: str | None = None) -> int:
    raw_meta = raw_meta or {}
    name = os.path.basename(file_path)
    print(f"\n⚙️  {name}")

    text, method = extract.extract(file_path)
    if not text:
        print("   ! rỗng / OCR fail, bỏ qua (KHÔNG xóa file).")
        return -1

    meta = metadata.extract_metadata(text, fallback=raw_meta)
    meta.update({
        "huong": huong, "file_name": name, "full_text": text,
        "source_url": source_url, "sha256": _sha256(file_path),
        "extract_method": method, "raw_meta": json.dumps(raw_meta, ensure_ascii=False),
    })

    # GIỮ bản gốc
    os.makedirs(config.STORE_DIR, exist_ok=True)
    dest = os.path.join(config.STORE_DIR, name)
    if os.path.abspath(dest) != os.path.abspath(file_path):
        shutil.copy2(file_path, dest)
    meta["file_path"] = dest

    chunks = split_text(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
    meta["n_chunks"] = len(chunks)

    doc_id = store.insert_document(meta)
    if doc_id == -1:
        print("   = đã tồn tại (sha256 trùng), bỏ qua.")
        return -1

    vecs = embedder.encode(chunks)
    points = []
    for chunk, v in zip(chunks, vecs):
        points.append(qm.PointStruct(
            id=str(uuid.uuid4()),
            vector={"dense": v["dense"],
                    "sparse": qm.SparseVector(indices=list(v["sparse"].keys()),
                                              values=list(v["sparse"].values()))},
            payload={"text": chunk, "doc_id": doc_id,
                     "so_ky_hieu": meta.get("so_ky_hieu"),
                     "ngay_ban_hanh": meta.get("ngay_ban_hanh"),
                     "loai_vb": meta.get("loai_vb"), "huong": huong,
                     "file_name": name,
                     "chu_truong": meta.get("chu_truong", []),
                     "linh_vuc": meta.get("linh_vuc", []),
                     "chuyen_de": meta.get("chuyen_de", []),},
        ))
    store.upsert_chunks(points)
    print(f"   ✓ doc_id={doc_id}, {len(chunks)} chunk -> Qdrant + Postgres")
    return doc_id


def ingest_download_dir():
    store.ensure_collection()
    files = [f for f in os.listdir(config.DOWNLOAD_DIR)
             if not f.endswith((".json",))]
    for f in files:
        fp = os.path.join(config.DOWNLOAD_DIR, f)
        meta_path = fp + ".meta.json"
        raw = {}
        if os.path.exists(meta_path):
            raw = json.load(open(meta_path, encoding="utf-8"))
        ingest_file(fp, huong=raw.get("huong", "di"), raw_meta=raw)


if __name__ == "__main__":
    ingest_download_dir()
