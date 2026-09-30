import os
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    value = int(os.getenv(name, default))
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


# --- Paths ---
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/opt/qlvb_ai/data/downloads")
STORE_DIR    = os.getenv("STORE_DIR", "/opt/qlvb_ai/data/store")  # nơi GIỮ bản gốc
CRAWLER_STATE_DB = os.getenv("CRAWLER_STATE_DB", os.path.join(STORE_DIR, "crawler_state.sqlite3"))

# --- Qdrant ---
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
RAG_COLLECTION = os.getenv("RAG_COLLECTION", "docnexus_agentic_v4")

# --- Postgres ---
PG_DSN = os.getenv(
    "PG_DSN",
    "host=localhost port=5432 dbname=qlvb user=qlvb password=changeme_pg",
)

# --- Embedding / rerank ---
EMBED_MODEL  = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
# auto | cuda | cpu — auto/cuda dùng GPU khi có, không có GPU thì tự quay về CPU (không lỗi).
EMBED_DEVICE = os.getenv("EMBEDDING_DEVICE", "auto").strip().lower() or "auto"
RERANK_MAX_LENGTH = _env_int("RERANK_MAX_LENGTH", 512, 128, 1024)
WARMUP_MODELS = _env_bool("WARMUP_MODELS", True)

# --- LLM (OpenAI-compatible) ---
# OpenRouter mặc định; đổi base_url + model sang Gemini OpenAI-compat nếu muốn.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY  = os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY")

# Định tuyến theo độ khó để tiết kiệm:
LLM_CHEAP = os.getenv("LLM_CHEAP", "google/gemini-2.5-flash-lite")  # metadata, lập kế hoạch, kiểm chứng, trích số liệu
LLM_MAIN  = os.getenv("LLM_MAIN",  "qwen/qwen-2.5-72b-instruct")    # trả lời search
LLM_SMART = os.getenv("LLM_SMART", "google/gemini-2.5-pro")        # kiểm tra nội dung/pháp lý
LLM_FALLBACK = os.getenv("LLM_FALLBACK", LLM_SMART)
# Tính năng cần loại đầu vào đặc biệt: gỡ băng cần model nhận âm thanh, OCR cần model nhận ảnh.
LLM_TRANSCRIBE = os.getenv("LLM_TRANSCRIBE", "gemini:gemini-2.5-flash")
LLM_OCR = os.getenv("LLM_OCR", "local:Unlimited-OCR")
LLM_MAX_OUTPUT_TOKENS = max(256, int(os.getenv("LLM_MAX_OUTPUT_TOKENS", 4096)))
# Lời gọi phụ trợ (planner, grader, verifier) có đầu ra ngắn; timeout riêng tránh một lượt chậm kéo dài P95.
LLM_FAST_TIMEOUT_SECONDS = _env_int("LLM_FAST_TIMEOUT_SECONDS", 20, 5)
# Stream trả lời: model im lặng quá ngưỡng này (trước token đầu hoặc giữa hai đoạn) thì chuyển model dự phòng
# nếu chưa phát token nào; cắt đuôi P95 khi provider chậm đột biến.
LLM_STREAM_IDLE_TIMEOUT_SECONDS = _env_int("LLM_STREAM_IDLE_TIMEOUT_SECONDS", 20, 5)

# --- Chunking / indexing ---
CHUNK_TOKENS = max(64, int(os.getenv("CHUNK_TOKENS", 512)))
CHUNK_OVERLAP_TOKENS = min(CHUNK_TOKENS // 4, max(0, int(os.getenv("CHUNK_OVERLAP_TOKENS", 64))))
EMBED_BATCH_SIZE = max(1, int(os.getenv("EMBED_BATCH_SIZE", 8)))
MODEL_CPU_THREADS = max(1, int(os.getenv("MODEL_CPU_THREADS", 4)))
RAG_CONCURRENCY = max(1, int(os.getenv("RAG_CONCURRENCY", 2)))
PG_POOL_SIZE = max(2, int(os.getenv("PG_POOL_SIZE", 8)))
QDRANT_TIMEOUT_SECONDS = max(1, int(os.getenv("QDRANT_TIMEOUT_SECONDS", 10)))
# Tệp trong cùng văn bản có tỷ lệ 3-gram trùng (độ bao chứa) từ ngưỡng này chỉ được index một lần.
DUPLICATE_FILE_SIMILARITY = float(os.getenv("DUPLICATE_FILE_SIMILARITY", 0.8))
INGEST_VERSION = "agentic-v4"
# Văn bản thiếu hướng Đi/Đến mà có ký hiệu cơ quan mình (ví dụ 215/KH-SKHCN) được coi là văn bản đi.
OWN_AGENCY_CODE = os.getenv("OWN_AGENCY_CODE", "SKHCN").strip().upper()

# --- Crawler reliability / bounded resources ---
CRAWLER_BATCH_SIZE = max(1, int(os.getenv("CRAWLER_BATCH_SIZE", 20)))
CRAWLER_NAVIGATION_RETRIES = max(1, int(os.getenv("CRAWLER_NAVIGATION_RETRIES", 3)))
CRAWLER_LOGIN_RETRIES = max(1, int(os.getenv("CRAWLER_LOGIN_RETRIES", 5)))
CRAWLER_PAGE_TIMEOUT_SECONDS = max(15, int(os.getenv("CRAWLER_PAGE_TIMEOUT_SECONDS", 90)))
CRAWLER_LOGIN_FORM_TIMEOUT_SECONDS = max(10, int(os.getenv("CRAWLER_LOGIN_FORM_TIMEOUT_SECONDS", 30)))
CRAWLER_LOGIN_INPUT_TIMEOUT_SECONDS = max(60, int(os.getenv("CRAWLER_LOGIN_INPUT_TIMEOUT_SECONDS", 600)))
CRAWLER_LOGIN_RESULT_TIMEOUT_SECONDS = max(10, int(os.getenv("CRAWLER_LOGIN_RESULT_TIMEOUT_SECONDS", 45)))
CRAWLER_ACTION_TIMEOUT_SECONDS = max(5, int(os.getenv("CRAWLER_ACTION_TIMEOUT_SECONDS", 20)))
CRAWLER_DOWNLOAD_TIMEOUT_SECONDS = max(10, int(os.getenv("CRAWLER_DOWNLOAD_TIMEOUT_SECONDS", 60)))
CRAWLER_MAX_PAGES = max(0, int(os.getenv("CRAWLER_MAX_PAGES", 0)))
# Hệ thống QLVB nội bộ có thể dùng chứng chỉ tự ký; đặt false khi chứng chỉ hợp lệ.
CRAWLER_IGNORE_HTTPS_ERRORS = _env_bool("CRAWLER_IGNORE_HTTPS_ERRORS", True)
QLVB_URL = os.getenv("QLVB_URL", "https://egov1.laocai.gov.vn").rstrip("/")
# SSO không công bố thời hạn captcha: tự đổi mã sau khoảng này; trang đăng nhập (phiên SSO) được mở lại
# sau CRAWLER_LOGIN_PAGE_MAX_AGE_SECONDS.
CRAWLER_CAPTCHA_TTL_SECONDS = _env_int("CRAWLER_CAPTCHA_TTL_SECONDS", 180, 30)
CRAWLER_LOGIN_PAGE_MAX_AGE_SECONDS = _env_int("CRAWLER_LOGIN_PAGE_MAX_AGE_SECONDS", 900, 120)

# --- Crawler qua API QLVB (khuyến nghị) ---
QLVB_API_BASE_URL = os.getenv("QLVB_API_BASE_URL", "https://egov-gateway.laocai.gov.vn").rstrip("/")
QLVB_STORAGE_HOSTS = {host.strip() for host in os.getenv(
    "QLVB_STORAGE_HOSTS", "egov-storage1.laocai.gov.vn,egov-storage.laocai.gov.vn").split(",") if host.strip()}
# Chứng chỉ trung gian GlobalSign mà máy chủ QLVB không gửi kèm (gateway: GCC R46 OV TLS CA 2025,
# máy lưu tệp: RSA OV SSL CA 2018). Khi QLVB đổi chứng chỉ, tải CA trung gian mới theo địa chỉ
# "CA Issuers" trong chứng chỉ máy chủ và thêm vào tệp này.
QLVB_CA_BUNDLE = os.getenv("QLVB_CA_BUNDLE", os.path.join(os.path.dirname(__file__), "..", "..", "certs",
                                                          "qlvb-intermediates.pem"))
# Cỡ trang mặc định khi kiểm kê; người dùng chọn lại trên trang admin (máy chủ QLVB cho tối đa 500).
CRAWLER_API_PAGE_SIZE = _env_int("CRAWLER_API_PAGE_SIZE", 100, 10, 500)
CRAWLER_API_MAX_PAGE_SIZE = 500
CRAWLER_API_DELAY_MS = _env_int("CRAWLER_API_DELAY_MS", 300, 0)
CRAWLER_API_TIMEOUT_SECONDS = _env_int("CRAWLER_API_TIMEOUT_SECONDS", 60, 10)
CRAWLER_API_MAX_ATTEMPTS = _env_int("CRAWLER_API_MAX_ATTEMPTS", 5, 1)
CRAWLER_MAX_FILE_MB = _env_int("CRAWLER_MAX_FILE_MB", 100, 1)
# Văn bản có độ mật khác "Thường" không được tải/gửi sang AI.
CRAWLER_SKIP_CLASSIFIED = _env_bool("CRAWLER_SKIP_CLASSIFIED", True)

# --- Agentic RAG ---
AGENT_ROUTING_MODE = os.getenv("AGENT_ROUTING_MODE", "selective").strip().lower()
RAG_FAST_MIN_SCORE = float(os.getenv("RAG_FAST_MIN_SCORE", 0.65))
AGENT_MAX_ATTEMPTS = max(1, min(3, int(os.getenv("AGENT_MAX_ATTEMPTS", 2))))
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", 60))
RAG_MIN_RERANK_SCORE = float(os.getenv("RAG_MIN_RERANK_SCORE", 0.1))
RAG_MIN_EVIDENCE = int(os.getenv("RAG_MIN_EVIDENCE", 2))
RAG_MULTI_QUERY_COUNT = max(1, min(4, int(os.getenv("RAG_MULTI_QUERY_COUNT", 3))))
RAG_MAX_CHUNKS_PER_DOC = max(1, int(os.getenv("RAG_MAX_CHUNKS_PER_DOC", 3)))
RAG_RERANK_POOL = _env_int("RAG_RERANK_POOL", 24, 8, 96)
# Mỗi khía cạnh của câu hỏi cần ít nhất một bằng chứng đạt điểm này để bỏ qua bước LLM chấm độ đủ.
RAG_ASPECT_MIN_SCORE = float(os.getenv("RAG_ASPECT_MIN_SCORE", 0.3))
RAG_LOG_QUERIES = _env_bool("RAG_LOG_QUERIES", True)

# --- Aggregation v2 ---
AGG_MAX_DOCS = _env_int("AGG_MAX_DOCS", 400, 1)
AGG_CHUNKS_PER_DOC = _env_int("AGG_CHUNKS_PER_DOC", 4, 1, 12)
AGG_CONCURRENCY = _env_int("AGG_CONCURRENCY", 4, 1, 16)
AGG_MIN_SCORE = float(os.getenv("AGG_MIN_SCORE", 0.2))
AGG_MIN_CONFIDENCE = float(os.getenv("AGG_MIN_CONFIDENCE", 0.6))
AGG_TIMEOUT_SECONDS = _env_int("AGG_TIMEOUT_SECONDS", 240, 30)

# --- Upload / tiện ích ---
UPLOAD_MAX_MB = _env_int("UPLOAD_MAX_MB", 25, 1)
AUDIO_MAX_MB = _env_int("AUDIO_MAX_MB", 60, 1)
UPLOAD_CONCURRENCY = _env_int("UPLOAD_CONCURRENCY", 2, 1)
OCR_SERVER_URL = os.getenv("OCR_SERVER_URL", "http://127.0.0.1:10000")
OCR_MAX_PAGES = _env_int("OCR_MAX_PAGES", 40, 1)
OCR_PAGES_PER_REQUEST = _env_int("OCR_PAGES_PER_REQUEST", 4, 1, 16)
OCR_DPI = _env_int("OCR_DPI", 200, 100, 400)

# --- Giới hạn trích xuất (chặn tệp "phình" bất thường làm hỏng lần nạp) ---
# Mỗi tệp / mỗi văn bản tối đa bấy nhiêu ký tự; vượt thì cắt bớt kèm ghi chú, không làm lỗi cả văn bản.
EXTRACT_MAX_FILE_CHARS = _env_int("EXTRACT_MAX_FILE_CHARS", 3_000_000, 50_000)
EXTRACT_MAX_DOCUMENT_CHARS = _env_int("EXTRACT_MAX_DOCUMENT_CHARS", 6_000_000, 100_000)
# Bảng tính: gặp chừng này dòng trống liên tiếp thì coi như hết sheet (vùng định dạng thừa tới cuối sheet).
EXCEL_EMPTY_ROW_STOP = _env_int("EXCEL_EMPTY_ROW_STOP", 500, 20)
# Nhắc lại tên cột sau mỗi chừng này dòng dữ liệu (thay vì lặp ở mọi dòng).
EXCEL_HEADER_EVERY = _env_int("EXCEL_HEADER_EVERY", 20, 1)
OCR_REQUEST_TIMEOUT_SECONDS = _env_int("OCR_REQUEST_TIMEOUT_SECONDS", 300, 30)

# --- Admin Auth ---
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "")
ADMIN_MAX_FAILED_LOGINS = _env_int("ADMIN_MAX_FAILED_LOGINS", 10, 3)
ADMIN_LOCKOUT_SECONDS = _env_int("ADMIN_LOCKOUT_SECONDS", 900, 60)
# Chỉ bật khi app đứng sau proxy tin cậy (Cloudflare Tunnel) để lấy IP thật của người dùng.
TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", False)

_WEAK_ADMIN_PASSWORDS = {"", "admin", "matkhau123", "password", "123456", "12345678", "changeme", "mat-khau-manh"}


def admin_password_is_weak() -> bool:
    return ADMIN_PASS.strip().lower() in _WEAK_ADMIN_PASSWORDS or len(ADMIN_PASS) < 10

SESSION_HOURS = _env_int("SESSION_HOURS", 12, 1, 24 * 7)
SESSION_REMEMBER_DAYS = _env_int("SESSION_REMEMBER_DAYS", 30, 1, 365)
USD_TO_VND = _env_int("USD_TO_VND", 26000, 1000)
