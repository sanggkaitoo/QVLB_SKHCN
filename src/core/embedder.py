"""bge-m3 cho HYBRID: vừa dense (semantic) vừa sparse (lexical/keyword).
Bản cũ chỉ dùng dense -> bỏ phí khả năng tìm theo từ khóa của bge-m3.
"""
from functools import lru_cache
from contextlib import contextmanager
from threading import Lock
from transformers import AutoTokenizer
import torch
from FlagEmbedding import BGEM3FlagModel, FlagReranker
from src.core import config
from src.core.runtime import remaining

_inference_lock = Lock()
torch.set_num_threads(config.MODEL_CPU_THREADS)


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
    print("[embed] nạp bge-m3...")
    return BGEM3FlagModel(config.EMBED_MODEL, use_fp16=False,
                          device=config.EMBED_DEVICE)


@lru_cache(maxsize=1)
def _reranker():
    print("[embed] nạp reranker...")
    return FlagReranker(config.RERANK_MODEL, use_fp16=False)


def encode(texts: list[str]) -> list[dict]:
    """Trả [{'dense': [...], 'sparse': {token_id: weight}}] cho từng text."""
    with inference_slot():
        out = _model().encode(texts, return_dense=True, return_sparse=True,
                              return_colbert_vecs=False, batch_size=config.EMBED_BATCH_SIZE)
    dense = out["dense_vecs"]
    sparse = out["lexical_weights"]   # list[dict[str,float]]
    res = []
    for i in range(len(texts)):
        sp = {int(k): float(v) for k, v in sparse[i].items() if v > 0}
        res.append({"dense": dense[i].tolist(), "sparse": sp})
    return res


def encode_one(text: str) -> dict:
    return encode([text])[0]


def rerank(query: str, passages: list[str]) -> list[float]:
    if not passages:
        return []
    pairs = [[query, p] for p in passages]
    with inference_slot():
        scores = _reranker().compute_score(pairs, normalize=True, batch_size=config.EMBED_BATCH_SIZE)
    return scores if isinstance(scores, list) else [scores]
