-- Văn bản (documents) và tệp (document_files) tách riêng: một văn bản có thể có
-- bản chính, bản sao cùng nội dung (PDF ký số, DOCX) và các tệp đính kèm.
-- Migration gom các bản ghi cũ (mỗi tệp một dòng documents) về một văn bản.
-- Sau migration cần chạy scripts/reindex.py để tạo index agentic-v4.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS source_key TEXT,
    ADD COLUMN IF NOT EXISTS vai_tro_van_ban TEXT,
    ADD COLUMN IF NOT EXISTS n_files INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE documents ALTER COLUMN file_name DROP NOT NULL;
ALTER TABLE documents ALTER COLUMN file_path DROP NOT NULL;
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_sha256_key;
CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);

CREATE TABLE IF NOT EXISTS document_files (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    file_index INT NOT NULL DEFAULT 0,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'dinh_kem' CHECK (role IN ('chinh', 'ban_sao', 'dinh_kem')),
    duplicate_of BIGINT REFERENCES document_files(id) ON DELETE SET NULL,
    extract_method TEXT,
    full_text TEXT,
    n_chunks INT NOT NULL DEFAULT 0,
    ingest_status TEXT NOT NULL DEFAULT 'pending',
    index_version TEXT,
    ingest_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_document_files_document ON document_files(document_id, file_index);
CREATE INDEX IF NOT EXISTS idx_document_files_sha256 ON document_files(sha256);

-- 1. Mỗi dòng documents cũ thành một tệp; id tệp giữ bằng id cũ để truy vết.
INSERT INTO document_files (id, document_id, file_index, file_name, file_path, sha256,
                            role, extract_method, full_text, n_chunks, ingest_status,
                            index_version, ingest_error)
SELECT d.id, d.id,
       COALESCE(substring(d.file_name FROM '_([0-9]{2})_')::int, 1),
       d.file_name, d.file_path, COALESCE(d.sha256, 'legacy-' || d.id::text),
       'chinh', d.extract_method, d.full_text, COALESCE(d.n_chunks, 0),
       'pending', d.index_version, d.ingest_error
FROM documents d
WHERE d.file_path IS NOT NULL
  AND d.file_name IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM document_files f WHERE f.id = d.id);

SELECT setval(pg_get_serial_sequence('document_files', 'id'),
              GREATEST((SELECT COALESCE(max(id), 0) FROM document_files), 1));

-- 2. Gom theo khóa crawler (history_key + hướng). Bản ghi không có khóa crawler
--    giữ nguyên từng văn bản (không đoán gộp theo số ký hiệu do LLM trích).
CREATE TEMP TABLE _doc_groups ON COMMIT DROP AS
SELECT id,
       key,
       min(id) OVER (PARTITION BY key) AS canonical_id
FROM (
    SELECT id,
           CASE
               WHEN NULLIF(raw_meta->>'history_key', '') IS NOT NULL
                   THEN COALESCE(huong, raw_meta->>'huong', 'di') || '|' || (raw_meta->>'history_key')
               ELSE 'sha256:' || COALESCE(sha256, id::text)
           END AS key
    FROM documents
    WHERE source_key IS NULL
) keyed;

UPDATE document_files f
SET document_id = g.canonical_id
FROM _doc_groups g
WHERE f.document_id = g.id AND g.id <> g.canonical_id;

-- Quan hệ sẽ được trích lại khi reindex; chỉ chuyển đích để không mất liên kết.
DELETE FROM document_relations r
USING _doc_groups g
WHERE r.source_document_id = g.id AND g.id <> g.canonical_id;

UPDATE document_relations r
SET target_document_id = g.canonical_id
FROM _doc_groups g
WHERE r.target_document_id = g.id AND g.id <> g.canonical_id;

UPDATE can_cu c
SET parent_id = g.canonical_id
FROM _doc_groups g
WHERE c.parent_id = g.id AND g.id <> g.canonical_id;

DELETE FROM documents d
USING _doc_groups g
WHERE d.id = g.id AND g.id <> g.canonical_id;

UPDATE documents d
SET source_key = g.key
FROM _doc_groups g
WHERE d.id = g.id AND g.id = g.canonical_id;

-- 3. Trạng thái: văn bản chờ reindex agentic-v4; tệp có index cũ cũng chờ.
UPDATE documents d
SET n_files = (SELECT count(*) FROM document_files f WHERE f.document_id = d.id),
    ingest_status = CASE WHEN d.index_version = 'agentic-v4' THEN d.ingest_status ELSE 'pending' END,
    updated_at = now();

CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_source_key ON documents(source_key);
