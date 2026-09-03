"""Pipeline nạp: extract -> AI metadata -> chunk -> embed(dense+sparse) ->
Qdrant + Postgres. GIỮ bản gốc (copy sang STORE_DIR).
Tích hợp Thuật toán Lọc 4 Lớp: Ưu tiên _Signed, Giữ Excel, Dọn Dự Thảo & Cách ly Căn cứ.
"""
import os
import json
import uuid
import shutil
import hashlib
import re
from collections import defaultdict
from qdrant_client import models as qm

from src.core import config, embedder, store
from src.utils import extract, metadata
from src.services.chunking import (
    build_structured_chunks, normalize_document_ref, stable_point_id,
)


def split_text(text: str, size: int, overlap: int,
               seps=("\nĐiều ", "\nKhoản ", "\n\n", "\n", ". ", " ")) -> list[str]:
    """Splitter đệ quy gọn (tôn trọng cấu trúc văn bản)."""
    if len(text) <= size:
        return [text] if text.strip() else []
    sep = next((s for s in seps if s in text), None)
    if sep is None:                       
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
            if len(buf) > size:           
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


def _archive_source(file_path: str, digest: str) -> str:
    target_dir = os.path.join(config.STORE_DIR, digest[:2], digest)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, os.path.basename(file_path))
    if os.path.abspath(file_path) != os.path.abspath(target) and not os.path.exists(target):
        shutil.copy2(file_path, target)
    return target

def ingest_file(file_path: str, huong: str = "di", raw_meta: dict | None = None,
                source_url: str | None = None) -> int:
    raw_meta = raw_meta or {}
    name = os.path.basename(file_path)
    print(f"\n⚙️  Đang xử lý: {name}")

    text, method = extract.extract(file_path)
    if not text:
        raise RuntimeError(
            f"Không trích xuất được nội dung từ {name}; giữ tệp để thử lại."
        )

    text = text.replace('\x00', '')
    meta = metadata.extract_metadata(text, fallback=raw_meta)
    
    # 1. Khóa cứng Số ký hiệu và Trích yếu
    meta["so_ky_hieu"] = raw_meta.get("so_ky_hieu", meta.get("so_ky_hieu"))
    meta["trich_yeu"] = raw_meta.get("trich_yeu", meta.get("trich_yeu"))
    
    # 2. Xử lý chuẩn hóa Ngày ban hành (Từ DD/MM/YYYY sang YYYY-MM-DD)
    raw_ngay = raw_meta.get("ngay_ban_hanh", "")
    if raw_ngay and "/" in raw_ngay:
        parts = raw_ngay.split("/")
        if len(parts) == 3:
            meta["ngay_ban_hanh"] = f"{parts[2].strip()}-{parts[1].strip().zfill(2)}-{parts[0].strip().zfill(2)}"
    elif raw_ngay:
        meta["ngay_ban_hanh"] = raw_ngay
    
    # 3. Khóa cứng Cơ quan ban hành (Dành cho Văn bản Đến)
    if raw_meta.get("co_quan_ban_hanh"):
        meta["co_quan_ban_hanh"] = raw_meta.get("co_quan_ban_hanh")

    digest = _sha256(file_path)
    archived_path = _archive_source(file_path, digest)
    meta["normalized_so_ky_hieu"] = normalize_document_ref(meta.get("so_ky_hieu"))
    meta.update({
        "huong": huong, 
        "file_name": name, 
        "full_text": text,
        "source_url": source_url, 
        "sha256": digest,
        "extract_method": method, 
        "raw_meta": json.dumps(raw_meta, ensure_ascii=False),
        "file_path": archived_path,
    })


    chunks = build_structured_chunks(
        text, config.CHUNK_SIZE, config.CHUNK_OVERLAP, doc_key=digest,
    )
    meta["n_chunks"] = len(chunks)

    doc_id = store.insert_document(meta)
    if doc_id == -1:
        raise RuntimeError("Không thể tạo hoặc lấy document_id cho tệp.")

    vecs = embedder.encode([chunk.text for chunk in chunks])
    points = []
    target_collection = config.RAG_COLLECTION
    for chunk, vector in zip(chunks, vecs):
        payload = {
            "text": chunk.text,
            "doc_id": doc_id,
            "so_ky_hieu": meta.get("so_ky_hieu"),
            "ngay_ban_hanh": meta.get("ngay_ban_hanh"),
            "loai_vb": meta.get("loai_vb"),
            "huong": huong,
            "file_name": name,
            "source_url": source_url,
            "co_quan_ban_hanh": meta.get("co_quan_ban_hanh"),
            "trich_yeu": meta.get("trich_yeu"),
            "chu_truong": meta.get("chu_truong", []),
            "linh_vuc": meta.get("linh_vuc", []),
            "chuyen_de": meta.get("chuyen_de", []),
            "tinh_trang_hieu_luc": meta.get("tinh_trang_hieu_luc", "chua_xac_dinh"),
            "hieu_luc_tu": meta.get("hieu_luc_tu"),
            "hieu_luc_den": meta.get("hieu_luc_den"),
            "chunk_index": chunk.chunk_index,
            "section_path": chunk.section_path,
            "parent_chunk_id": chunk.parent_chunk_id,
            "parent_text": chunk.parent_text,
            "heading": chunk.heading,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "chunk_kind": "child",
        }
        points.append(qm.PointStruct(
            id=stable_point_id(target_collection, doc_id, chunk.chunk_index, chunk.text),
            vector={
                "dense": vector["dense"],
                "sparse": qm.SparseVector(
                    indices=list(vector["sparse"].keys()), values=list(vector["sparse"].values())
                ),
            },
            payload=payload,
        ))
    store.ensure_collection(target_collection)
    store.upsert_chunks(points, target_collection)
    if target_collection != config.QDRANT_COLLECTION:
        store.ensure_collection(config.QDRANT_COLLECTION)
        store.upsert_chunks(points, config.QDRANT_COLLECTION)
    print(f"   ✓ Thành công! doc_id={doc_id}, sinh ra {len(chunks)} chunks -> Đã lưu Qdrant & Postgres")
    return doc_id


def ingest_download_dir(download_dir: str | None = None):
    download_dir = download_dir or config.DOWNLOAD_DIR
    store.ensure_collection()
    print("\n[AI INGEST] BẮT ĐẦU PHÂN TÍCH VÀ NẠP DỮ LIỆU...")
    
    from collections import defaultdict
    import re
    
    # 1. Gom nhóm file theo tuple (Số ký hiệu, Hướng) để không bị trộn lẫn Đi/Đến
    groups = defaultdict(list)
    meta_files = [f for f in os.listdir(download_dir) if f.endswith(".meta.json")]
    
    for mf in meta_files:
        meta_path = os.path.join(download_dir, mf)
        file_path = meta_path.replace(".meta.json", "")
        if not os.path.exists(file_path): continue 
            
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                raw_meta = json.load(f)
        except: continue
                
        so_ky_hieu = raw_meta.get("so_ky_hieu", f"unknown_{mf}")
        huong = raw_meta.get("huong", "di")
        
        groups[(so_ky_hieu, huong)].append({
            "path": file_path,
            "name": os.path.basename(file_path).lower(),
            "ext": os.path.splitext(file_path)[1].lower(),
            "size": os.path.getsize(file_path),
            "meta": raw_meta
        })

    # 2. XỬ LÝ LỌC THEO HƯỚNG VĂN BẢN (DI / DEN)
    for (so_ky_hieu, huong), files in groups.items():
        print(f"\n🔍 [SÀNG LỌC] VB {huong.upper()}: {so_ky_hieu} ({len(files)} files)")
        final_files_to_ingest = []
        
        pdfs = [f for f in files if f["ext"] == ".pdf"]
        excels = [f for f in files if f["ext"] in [".xls", ".xlsx", ".csv"]]
        words = [f for f in files if f["ext"] in [".doc", ".docx"]]

        if huong == "di":
            # --- LUẬT CỦA VĂN BẢN ĐI (Bóp nghẹt) ---
            num_match = re.search(r'\d+', so_ky_hieu)
            doc_num = num_match.group(0) if num_match else ""
            
            valid_pdfs = []
            for p in pdfs:
                if any(k in p["name"] for k in ["can_cu", "cancu", "thamkhao"]): continue 
                if "signed" not in p["name"] and doc_num and doc_num not in p["name"]: continue 
                valid_pdfs.append(p)

            if valid_pdfs:
                signed_pdfs = [f for f in valid_pdfs if "signed" in f["name"]]
                main_doc = sorted(signed_pdfs or valid_pdfs, key=lambda x: x["size"], reverse=True)[0]
                final_files_to_ingest.append(main_doc)
            elif words:
                final_files_to_ingest.append(sorted(words, key=lambda x: x["size"], reverse=True)[0])
            
            final_files_to_ingest.extend(excels)

        else:
            # --- LUẬT CỦA VĂN BẢN ĐẾN (Heuristic) ---
            # 1. Luôn giữ Excel
            final_files_to_ingest.extend(excels)
            
            # 2. Khử Phiếu gửi, Phiếu chuyển bằng PDF
            filtered_pdfs = []
            for p in pdfs:
                if any(k in p["name"] for k in ["phieu_gui", "phieu_chuyen", "ticket", "luanchuyen"]):
                    continue
                filtered_pdfs.append(p)
                final_files_to_ingest.append(p)
                
            # 3. Gom cặp bài trùng: Nếu có cả Bản PDF và Bản Word trùng tên gốc, Xóa bản Word
            # ĐÃ NÂNG CẤP: Xóa các tiền tố "0_", "1_" do hệ thống QLVB tự sinh ra để so sánh chuẩn xác
            def clean_basename(filename):
                base = os.path.splitext(filename)[0]
                # Dùng Regex r'^\d+_' để tìm và xóa các số theo sau là dấu gạch dưới ở ĐẦU chuỗi
                return re.sub(r'^\d+_', '', base)
                
            pdf_basenames = {clean_basename(p["name"]) for p in filtered_pdfs}
            for w in words:
                if clean_basename(w["name"]) not in pdf_basenames: 
                    final_files_to_ingest.append(w) # Chỉ lấy Word nếu ko có PDF trùng tên

        # 3. KÍCH HOẠT NẠP AI & DỌN DẸP Ổ CỨNG
        keep_paths = [f["path"] for f in final_files_to_ingest]
        for f in files:
            if f["path"] not in keep_paths:
                print(f"   🗑️ Đã lọc bỏ: {f['name']}")

            if f["path"] in keep_paths:
                ingest_file(
                    f["path"],
                    huong=huong,
                    raw_meta=f["meta"],
                    source_url=f["meta"].get("source_url"),
                )
            
            try:
                os.remove(f["path"])
                os.remove(f["path"] + ".meta.json")
            except: pass

if __name__ == "__main__":
    ingest_download_dir()