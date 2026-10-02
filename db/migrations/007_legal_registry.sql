-- Danh mục văn bản pháp luật từ CSDL quốc gia về pháp luật (vbpl.vn) cho "Kiểm tra nội dung & căn cứ".
-- Chỉ thêm bảng mới; không thay đổi bảng hiện có.

-- Địa chỉ văn bản lấy từ sitemap công khai của vbpl.vn (Trung ương + địa phương Lào Cai/Yên Bái).
CREATE TABLE IF NOT EXISTS legal_pages (
    url TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('central', 'local')),
    num_year TEXT,                 -- "30/2020" trích từ đầu địa chỉ; NULL nếu địa chỉ không có số
    slug TEXT NOT NULL,            -- phần chữ của địa chỉ (tìm theo tên khi không có số)
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_legal_pages_num_year ON legal_pages(num_year);

-- Thông tin đọc từ dữ liệu cấu trúc (schema.org/Legislation) trên trang chi tiết; dùng làm bộ nhớ đệm.
CREATE TABLE IF NOT EXISTS legal_documents (
    url TEXT PRIMARY KEY,
    identifier TEXT,               -- "30/2020/NĐ-CP"
    normalized_identifier TEXT,    -- "30/2020/ND-CP"
    doc_type TEXT,                 -- "Nghị định"
    issued DATE,
    legal_force TEXT,              -- InForce | PartiallyInForce | NotInForce | NotYetInForce …
    issuer TEXT,
    name TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_legal_documents_identifier ON legal_documents(normalized_identifier);
