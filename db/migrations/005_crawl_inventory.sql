-- Sổ theo dõi crawler API: mỗi văn bản trên QLVB một dòng, khoá là mã nội bộ (macongvan) theo hướng.
-- Trạng thái: pending (chưa tải) -> processing -> done | failed | skipped. Ghi sau từng văn bản nên
-- mất mạng/tắt máy chỉ mất văn bản đang xử lý; lần chạy sau tiếp tục phần còn lại.

CREATE TABLE IF NOT EXISTS crawl_items (
    id BIGSERIAL PRIMARY KEY,
    direction TEXT NOT NULL CHECK (direction IN ('di', 'den')),
    source_id TEXT NOT NULL,
    so_ky_hieu TEXT,
    trich_yeu TEXT,
    ngay DATE,
    co_quan TEXT,
    loai_vb TEXT,
    do_mat TEXT,
    file_names TEXT[] NOT NULL DEFAULT '{}',
    list_fingerprint TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'done', 'failed', 'skipped')),
    skip_reason TEXT,
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    document_id BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    n_files INT NOT NULL DEFAULT 0,
    n_files_ingested INT NOT NULL DEFAULT 0,
    in_source BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    UNIQUE (direction, source_id)
);

CREATE INDEX IF NOT EXISTS idx_crawl_items_status ON crawl_items(direction, status);
CREATE INDEX IF NOT EXISTS idx_crawl_items_ngay ON crawl_items(ngay DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_crawl_items_soky ON crawl_items(so_ky_hieu);

CREATE TABLE IF NOT EXISTS crawl_sweeps (
    id BIGSERIAL PRIMARY KEY,
    direction TEXT NOT NULL,
    mode TEXT NOT NULL,                 -- quick | full
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    source_total INT,
    seen INT NOT NULL DEFAULT 0,
    new_items INT NOT NULL DEFAULT 0,
    changed_items INT NOT NULL DEFAULT 0,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_crawl_sweeps_direction ON crawl_sweeps(direction, started_at DESC);
