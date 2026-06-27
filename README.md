# QLVB AI v2 — Phase 1 (Search + Aggregate) & Phase 2 (Check)

Xây lại từ `RAG_QLVB`. Giữ phần RPA crawler (Playwright) của bản cũ; thay phần lõi RAG.

## Thay đổi chính so với bản cũ

| Vấn đề bản cũ | Bản v2 |
|---|---|
| Search **chỉ dense** (`limit=4`) → miss từ khóa, số ký hiệu | **Hybrid** dense + sparse (bge-m3) hợp nhất **RRF** + **rerank** |
| **Xóa file gốc** sau ingest | **Giữ** bản gốc (`STORE_DIR`) — cần cho check & tổng hợp |
| Chỉ có chunk trong Qdrant, không có doc-level | Thêm **Postgres** `documents` (lọc theo loại/cơ quan/ngày, fuzzy số ký hiệu) |
| Metadata chỉ từ `.meta.json` | **LLM trích metadata** (loại VB, cơ quan, người ký, trích yếu) |
| Không tổng hợp được đa văn bản | **Map-reduce** trích từng VB → code cộng → **kèm minh chứng** |
| Không kiểm tra văn bản | **Phase 2**: check định dạng (NĐ 30/2020) + nội dung/căn cứ |
| Qdrant 1.9 (chưa có Query API) | **Qdrant 1.12** (RRF fusion) |

## Kiến trúc

```
crawler (giữ nguyên) ─► downloads/ ─► ingest.py
                                         │ extract → AI metadata → chunk
                                         │ embed dense+sparse (bge-m3)
                                         ▼
                              Qdrant (chunk) + Postgres (doc-level)
                                         ▲
        ┌────────────────────────────────┼─────────────────────────────┐
   search.py (hybrid+rerank)   aggregate.py (map-reduce)   check/ (format+content)
        └────────────────────────────── api.py (FastAPI) ───────────────┘
```

## Chạy

```bash
cp .env.example .env          # điền API key, mật khẩu
docker compose up -d          # Qdrant + Postgres (tự nạp schema.sql)
pip install -r requirements.txt
python -m playwright install chromium    # nếu dùng crawler

# nạp dữ liệu đã tải về
python -m src.ingest

# API
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

## Endpoint

| | |
|---|---|
| `GET /api/search_stream?q=...&loai_vb=&huong=` | tìm kiếm hybrid + trả lời grounded (stream) |
| `GET /api/aggregate?q=...` | tổng hợp đa văn bản, trả `total` + `evidence[]` |
| `POST /api/check/format` (file .docx) | lỗi font/cỡ chữ/lề/giãn dòng theo NĐ 30/2020 |
| `POST /api/check/content` (file) | căn cứ + chính tả/logic (RAG, có dẫn nguồn) |

Sửa quy định định dạng tại `config/format_rules.yaml` (không hard-code).

## Lưu ý bảo mật
LLM mặc định gọi API ngoài (OpenRouter/Gemini). Văn bản Mật/Tối mật KHÔNG đưa vào
pipeline này, hoặc trỏ `LLM_*` sang model local. Phân loại độ mật khi crawl.

## Chưa làm (Phase 3)
Bảng `can_cu` đã tạo sẵn trong schema. Truy vết căn cứ tận gốc (recursive CTE)
và biểu nhiệm vụ — làm sau khi corpus đã sạch.
```
```
