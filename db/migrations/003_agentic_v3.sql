ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS ingest_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS index_version TEXT,
    ADD COLUMN IF NOT EXISTS ingest_error TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_ready ON documents(ingest_status, index_version);
