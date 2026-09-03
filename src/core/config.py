import os
from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/opt/qlvb_ai/data/downloads")
STORE_DIR    = os.getenv("STORE_DIR", "/opt/qlvb_ai/data/store")  # nơi GIỮ bản gốc
CRAWLER_STATE_DB = os.getenv("CRAWLER_STATE_DB", os.path.join(STORE_DIR, "crawler_state.sqlite3"))

# --- Qdrant ---
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "qlvb_docs")
RAG_COLLECTION = os.getenv("RAG_COLLECTION", QDRANT_COLLECTION)

# --- Postgres ---
PG_DSN = os.getenv(
    "PG_DSN",
    "host=localhost port=5432 dbname=qlvb user=qlvb password=changeme_pg",
)

# --- Embedding / rerank ---
EMBED_MODEL  = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
EMBED_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")

# --- LLM (OpenAI-compatible) ---
# OpenRouter mặc định; đổi base_url + model sang Gemini OpenAI-compat nếu muốn.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY  = os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY")

# Định tuyến theo độ khó để tiết kiệm:
LLM_CHEAP = os.getenv("LLM_CHEAP", "google/gemini-2.5-flash-lite")  # trích metadata, map-reduce
LLM_MAIN  = os.getenv("LLM_MAIN",  "qwen/qwen-2.5-72b-instruct")    # trả lời search
LLM_SMART = os.getenv("LLM_SMART", "google/gemini-2.5-pro")        # kiểm tra nội dung/pháp lý
LLM_FALLBACK = os.getenv("LLM_FALLBACK", LLM_SMART)

# --- Chunking ---
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1000))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 200))

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

def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


AGENTIC_RAG_ENABLED = _env_bool("AGENTIC_RAG_ENABLED", False)
AGENT_MAX_ATTEMPTS = max(1, min(3, int(os.getenv("AGENT_MAX_ATTEMPTS", 2))))
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", 20))
RAG_MIN_RERANK_SCORE = float(os.getenv("RAG_MIN_RERANK_SCORE", 0.1))
RAG_MIN_EVIDENCE = int(os.getenv("RAG_MIN_EVIDENCE", 2))
RAG_MULTI_QUERY_COUNT = max(1, min(4, int(os.getenv("RAG_MULTI_QUERY_COUNT", 3))))
RAG_MAX_CHUNKS_PER_DOC = max(1, int(os.getenv("RAG_MAX_CHUNKS_PER_DOC", 3)))
RAG_LOG_QUERIES = _env_bool("RAG_LOG_QUERIES", True)

# --- Admin Auth ---
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "matkhau123")