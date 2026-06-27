import os
from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/opt/qlvb_ai/data/downloads")
STORE_DIR    = os.getenv("STORE_DIR", "/opt/qlvb_ai/data/store")  # nơi GIỮ bản gốc

# --- Qdrant ---
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "qlvb_docs")

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

# --- Chunking ---
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1000))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 200))
