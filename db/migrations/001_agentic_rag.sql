ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS normalized_so_ky_hieu TEXT,
    ADD COLUMN IF NOT EXISTS tinh_trang_hieu_luc TEXT DEFAULT 'chua_xac_dinh',
    ADD COLUMN IF NOT EXISTS hieu_luc_tu DATE,
    ADD COLUMN IF NOT EXISTS hieu_luc_den DATE,
    ADD COLUMN IF NOT EXISTS extract_confidence REAL,
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;

UPDATE documents
SET normalized_so_ky_hieu = upper(regexp_replace(translate(so_ky_hieu, 'đĐ', 'dD'), '\s+', '', 'g'))
WHERE so_ky_hieu IS NOT NULL
  AND (normalized_so_ky_hieu IS NULL OR normalized_so_ky_hieu = '');

CREATE INDEX IF NOT EXISTS idx_doc_normalized_soky
    ON documents(normalized_so_ky_hieu);
CREATE INDEX IF NOT EXISTS idx_doc_hieu_luc
    ON documents(tinh_trang_hieu_luc, hieu_luc_tu, hieu_luc_den);

CREATE TABLE IF NOT EXISTS document_relations (
    id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    target_document_id BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    target_ref_text TEXT,
    normalized_target_ref TEXT,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN ('can_cu', 'sua_doi', 'thay_the', 'bai_bo', 'lien_quan')
    ),
    evidence_text TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_document_id, relation_type, target_ref_text)
);

CREATE INDEX IF NOT EXISTS idx_doc_rel_source ON document_relations(source_document_id);
CREATE INDEX IF NOT EXISTS idx_doc_rel_target ON document_relations(target_document_id);
CREATE INDEX IF NOT EXISTS idx_doc_rel_ref ON document_relations(normalized_target_ref);

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

CREATE INDEX IF NOT EXISTS idx_rag_logs_created_at ON rag_query_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_logs_intent ON rag_query_logs(intent);
