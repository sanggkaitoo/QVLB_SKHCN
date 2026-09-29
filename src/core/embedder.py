"""bge-m3 cho HYBRID: vừa dense (semantic) vừa sparse (lexical/keyword), cùng reranker."""
from collections import OrderedDict
from functools import lru_cache
from contextlib import contextmanager
import logging
from threading import Lock
from transformers import AutoTokenizer
import torch
from FlagEmbedding import BGEM3FlagModel, FlagReranker
from src.core import config
from src.core.runtime import remaining

logger = logging.getLogger(__name__)

_inference_lock = Lock()
torch.set_num_threads(config.MODEL_CPU_THREADS)

# Câu hỏi lặp lại (người dùng bấm lại, vòng agent thứ hai) không phải encode lại.
_QUERY_CACHE_SIZE = 512
_query_cache: OrderedDict[str, dict] = OrderedDict()
_query_cache_lock = Lock()


@contextmanager
def inference_slot():
    if not _inference_lock.acquire(timeout=remaining(300)):
        raise TimeoutError("Model worker is busy")
    try:
        remaining(300)
        yield
        remaining(300)
    finally:
        _inference_lock.release()


@lru_cache(maxsize=1)
def tokenizer():
    return AutoTokenizer.from_pretrained(config.EMBED_MODEL, use_fast=True)


@lru_cache(maxsize=1)
def _model():
    logger.info("Loading embedding model %s", config.EMBED_MODEL)
    return BGEM3FlagModel(config.EMBED_MODEL, use_fp16=False,
                          device=config.EMBED_DEVICE)


@lru_cache(maxsize=1)
def _reranker():
    logger.info("Loading reranker %s", config.RERANK_MODEL)
    return FlagReranker(config.RERANK_MODEL, use_fp16=False)


def warmup() -> None:
    """Load models and run one tiny inference so the first real request is not a cold start."""
    tokenizer()
    encode(["khởi động"])
    rerank_pairs([("khởi động", "khởi động")])
    logger.info("Embedding and rerank models are warm")


def encode(texts: list[str], max_length: int = 1024) -> list[dict]:
    """Trả [{'dense': [...], 'sparse': {token_id: weight}}] cho từng text."""
    if not texts:
        return []
    with inference_slot():
        out = _model().encode(texts, return_dense=True, return_sparse=True,
                              return_colbert_vecs=False, batch_size=config.EMBED_BATCH_SIZE,
                              max_length=max_length)
    dense = out["dense_vecs"]
    sparse = out["lexical_weights"]   # list[dict[str,float]]
    res = []
    for i in range(len(texts)):
        sp = {int(k): float(v) for k, v in sparse[i].items() if v > 0}
        res.append({"dense": dense[i].tolist(), "sparse": sp})
    return res


def encode_queries(queries: list[str]) -> list[dict]:
    """Encode short queries with an LRU cache; misses are encoded in one batch."""
    keys = [" ".join(query.split()) for query in queries]
    results: dict[str, dict] = {}
    with _query_cache_lock:
        for key in keys:
            if key in _query_cache:
                _query_cache.move_to_end(key)
                results[key] = _query_cache[key]
    missing = list(dict.fromkeys(key for key in keys if key not in results))
    if missing:
        for key, vector in zip(missing, encode(missing, max_length=512)):
            results[key] = vector
        with _query_cache_lock:
            for key in missing:
                _query_cache[key] = results[key]
                _query_cache.move_to_end(key)
            while len(_query_cache) > _QUERY_CACHE_SIZE:
                _query_cache.popitem(last=False)
    return [results[key] for key in keys]


def encode_one(text: str) -> dict:
    return encode_queries([text])[0]


def rerank_pairs(pairs: list[tuple[str, str]]) -> list[float]:
    if not pairs:
        return []
    with inference_slot():
        scores = _reranker().compute_score([list(pair) for pair in pairs], normalize=True,
                                           batch_size=config.EMBED_BATCH_SIZE,
                                           max_length=config.RERANK_MAX_LENGTH)
    return [float(score) for score in scores] if isinstance(scores, list) else [float(scores)]


def rerank(query: str, passages: list[str]) -> list[float]:
    return rerank_pairs([(query, passage) for passage in passages])
