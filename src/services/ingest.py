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


def ingest_file(file_path: str, huong: str = "di", raw_meta: dict | None = None,
                source_url: str | None = None) -> int:
    raw_meta = raw_meta or {}
    name = os.path.basename(file_path)
    print(f"\n⚙️  Đang xử lý: {name}")

    text, method = extract.extract(file_path)
    if not text:
        print("   ! rỗng / OCR fail, bỏ qua (KHÔNG xóa file).")
        return -1

    meta = metadata.extract_metadata(text, fallback=raw_meta)

    meta["so_ky_hieu"] = raw_meta.get("so_ky_hieu", meta.get("so_ky_hieu"))
    meta["ngay_ban_hanh"] = raw_meta.get("ngay_ban_hanh", meta.get("ngay_ban_hanh"))
    meta["trich_yeu"] = raw_meta.get("trich_yeu", meta.get("trich_yeu"))

    if raw_meta.get("co_quan_ban_hanh"):
        meta["co_quan_ban_hanh"] = raw_meta.get("co_quan_ban_hanh")

    meta.update({
        "huong": huong, "file_name": name, "full_text": text,
        "source_url": source_url, "sha256": _sha256(file_path),
        "extract_method": method, "raw_meta": json.dumps(raw_meta, ensure_ascii=False),
    })

    # GIỮ bản gốc vào kho lưu trữ an toàn (STORE_DIR)
    os.makedirs(config.STORE_DIR, exist_ok=True)
    dest = os.path.join(config.STORE_DIR, name)
    if os.path.abspath(dest) != os.path.abspath(file_path):
        shutil.copy2(file_path, dest)
    meta["file_path"] = dest

    chunks = split_text(text, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
    meta["n_chunks"] = len(chunks)

    doc_id = store.insert_document(meta)
    if doc_id == -1:
        print("   = Đã tồn tại trong hệ thống (sha256 trùng), bỏ qua.")
        return -1

    vecs = embedder.encode(chunks)
    points = []
    for chunk, v in zip(chunks, vecs):
        # THÊM TRƯỜNG vai_tro_van_ban VÀO ĐỂ TÌM KIẾM THEO GIAO VIỆC / BÁO CÁO
        points.append(qm.PointStruct(
            id=str(uuid.uuid4()),
            vector={"dense": v["dense"],
                    "sparse": qm.SparseVector(indices=list(v["sparse"].keys()),
                                              values=list(v["sparse"].values()))},
            payload={
                "text": chunk, 
                "doc_id": doc_id,
                "so_ky_hieu": meta.get("so_ky_hieu"),
                "ngay_ban_hanh": meta.get("ngay_ban_hanh"),
                "loai_vb": meta.get("loai_vb"), 
                "huong": huong,
                "file_name": name,
                "co_quan_ban_hanh": meta.get("co_quan_ban_hanh"),
                "chu_truong": meta.get("chu_truong", []),
                "linh_vuc": meta.get("linh_vuc", []),
                "vai_tro_van_ban": meta.get("vai_tro_van_ban", "khac"), 
                "chuyen_de": meta.get("chuyen_de", []),
            },
        ))
    store.upsert_chunks(points)
    print(f"   ✓ Thành công! doc_id={doc_id}, sinh ra {len(chunks)} chunks -> Đã lưu Qdrant & Postgres")
    return doc_id


def ingest_download_dir():
    store.ensure_collection()
    print("\n[AI INGEST] BẮT ĐẦU PHÂN TÍCH VÀ NẠP DỮ LIỆU...")
    
    from collections import defaultdict
    import re
    
    # 1. Gom nhóm file theo tuple (Số ký hiệu, Hướng) để không bị trộn lẫn Đi/Đến
    groups = defaultdict(list)
    meta_files = [f for f in os.listdir(config.DOWNLOAD_DIR) if f.endswith(".meta.json")]
    
    for mf in meta_files:
        meta_path = os.path.join(config.DOWNLOAD_DIR, mf)
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
            pdf_basenames = {os.path.splitext(p["name"])[0] for p in filtered_pdfs}
            for w in words:
                basename = os.path.splitext(w["name"])[0]
                if basename not in pdf_basenames: 
                    final_files_to_ingest.append(w) # Chỉ lấy Word nếu ko có PDF trùng tên

        # 3. KÍCH HOẠT NẠP AI & DỌN DẸP Ổ CỨNG
        keep_paths = [f["path"] for f in final_files_to_ingest]
        for f in files:
            if f["path"] not in keep_paths:
                print(f"   🗑️ Đã lọc bỏ: {f['name']}")

            if f["path"] in keep_paths:
                ingest_file(f["path"], huong=huong, raw_meta=f["meta"])
            
            try:
                os.remove(f["path"])
                os.remove(f["path"] + ".meta.json")
            except: pass

if __name__ == "__main__":
    ingest_download_dir()