-- =====================================================================
--  QLVB v2 - System of record (Postgres)
--  Giữ bản gốc + metadata có cấu trúc để LỌC và TỔNG HỢP.
--  (Vector/chunk nằm ở Qdrant; Postgres giữ doc-level.)
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy match số ký hiệu

CREATE TABLE IF NOT EXISTS documents (
    id              BIGSERIAL PRIMARY KEY,
    -- định danh
    so_ky_hieu      TEXT,                 -- "215/KH-UBND"
    ngay_ban_hanh   DATE,
    -- 29 loại VB hành chính theo NĐ 30/2020/NĐ-CP (Điều 7):
    -- nghi_quyet(NQ), quyet_dinh(QĐ), chi_thi(CT), quy_che(QC), quy_dinh(QYĐ),
    -- thong_cao(TC), thong_bao(TB), huong_dan(HD), chuong_trinh(CTr), ke_hoach(KH),
    -- phuong_an(PA), de_an(ĐA), du_an(DA), bao_cao(BC), bien_ban(BB),
    -- to_trinh(TTr), hop_dong(HĐ), cong_van(CV), cong_dien(CĐ), ban_ghi_nho(BGN),
    -- ban_thoa_thuan(BTT), giay_uy_quyen(GUQ), giay_moi(GM), giay_gioi_thieu(GGT),
    -- giay_nghi_phep(GNP), phieu_gui(PG), phieu_chuyen(PC), phieu_bao(PB),
    -- thu_cong(TC2), khac
    loai_vb         TEXT,
    viet_tat_loai   TEXT,                 -- viết tắt NĐ30: KH, QĐ, CV, BC…
    chu_truong      TEXT[],               -- ['Nghị quyết 57-NQ/TW','Đề án 06']
    linh_vuc        TEXT[],               -- enum subset: khoa_hoc, cong_nghe, dmst, cds, tdđlcl, bcvt
    chuyen_de       TEXT[],               -- ['hạ tầng số','sở hữu trí tuệ','dự án đầu tư công']
    huong           TEXT,                 -- 'di' | 'den'
    co_quan_ban_hanh TEXT,
    nguoi_ky        TEXT,
    chuc_vu_nguoi_ky TEXT,
    trich_yeu       TEXT,
    -- nội dung
    file_name       TEXT NOT NULL,
    file_path       TEXT NOT NULL,        -- GIỮ bản gốc, KHÔNG xóa
    full_text       TEXT,
    -- truy vết
    source_url      TEXT,
    sha256          TEXT UNIQUE,          -- chống nạp trùng
    extract_method  TEXT,                 -- pdf_text | ocr_tesseract | docx | doc_libre | xlsx
    n_chunks        INT DEFAULT 0,
    ingested_at     TIMESTAMPTZ DEFAULT now(),
    raw_meta        JSONB                 -- .meta.json gốc từ crawler
);

CREATE INDEX IF NOT EXISTS idx_doc_loai      ON documents(loai_vb);
CREATE INDEX IF NOT EXISTS idx_doc_huong     ON documents(huong);
CREATE INDEX IF NOT EXISTS idx_doc_ngay      ON documents(ngay_ban_hanh);
CREATE INDEX IF NOT EXISTS idx_doc_coquan    ON documents(co_quan_ban_hanh);
CREATE INDEX IF NOT EXISTS idx_doc_soky_trgm ON documents USING gin (so_ky_hieu gin_trgm_ops);
-- full-text tiếng Việt (đơn giản; nâng cấp = unaccent/zalo config sau)
CREATE INDEX IF NOT EXISTS idx_doc_fts        ON documents USING gin (to_tsvector('simple', coalesce(full_text,'')));
CREATE INDEX IF NOT EXISTS idx_doc_linhvuc  ON documents USING gin (linh_vuc);
CREATE INDEX IF NOT EXISTS idx_doc_chutruong ON documents USING gin (chu_truong);
CREATE INDEX IF NOT EXISTS idx_doc_chuyende ON documents USING gin (chuyen_de);

-- ---------------------------------------------------------------------
--  ĐỒ THỊ CĂN CỨ (để Phase 3; tạo sẵn để crawler/ingest nạp dần)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS can_cu (
    child_id        BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    parent_ref_text TEXT,                 -- chuỗi trích được: "Nghị quyết 57-NQ/TW ngày 22/12/2024"
    parent_so_ky    TEXT,                 -- chuẩn hóa: "57/NQ-TW"
    parent_id       BIGINT REFERENCES documents(id) ON DELETE SET NULL, -- resolve được thì gán
    PRIMARY KEY (child_id, parent_ref_text)
);
CREATE INDEX IF NOT EXISTS idx_cancu_parent ON can_cu(parent_id);


-- Agentic RAG metadata for fresh installations.
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS normalized_so_ky_hieu TEXT,
    ADD COLUMN IF NOT EXISTS tinh_trang_hieu_luc TEXT DEFAULT 'chua_xac_dinh',
    ADD COLUMN IF NOT EXISTS hieu_luc_tu DATE,
    ADD COLUMN IF NOT EXISTS hieu_luc_den DATE,
    ADD COLUMN IF NOT EXISTS extract_confidence REAL,
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_doc_normalized_soky ON documents(normalized_so_ky_hieu);

CREATE TABLE IF NOT EXISTS document_relations (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    target_document_id BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    target_ref_text TEXT,
    normalized_target_ref TEXT,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('can_cu','sua_doi','thay_the','bai_bo','lien_quan')),
    evidence_text TEXT,
    confidence REAL,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_document_id, relation_type, target_ref_text)
);

CREATE TABLE IF NOT EXISTS rag_query_logs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    query_text TEXT,
    intent TEXT,
    plan JSONB,
    source_doc_ids BIGINT[],
    rerank_scores REAL[],
    attempts INT NOT NULL DEFAULT 1,
    confidence TEXT,
    latency_ms INT,
    answer_status TEXT,
    error_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_rel_source ON document_relations(source_document_id);
CREATE INDEX IF NOT EXISTS idx_doc_rel_target ON document_relations(target_document_id);
CREATE INDEX IF NOT EXISTS idx_doc_rel_ref ON document_relations(normalized_target_ref);
CREATE INDEX IF NOT EXISTS idx_rag_logs_created_at ON rag_query_logs(created_at DESC);
