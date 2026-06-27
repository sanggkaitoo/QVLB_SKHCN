"""Tìm kiếm hybrid (dense + sparse RRF) -> rerank -> trả lời grounded.
Nâng cấp so với bản cũ: thêm keyword (sparse), rerank, trích nguồn số ký hiệu.
"""
from qdrant_client import models as qm
from . import embedder, store, llm, config

_SYS = """Bạn là trợ lý pháp lý của Sở Khoa học và Công nghệ.
QUY TẮC:
1. CHỈ trả lời dựa trên các đoạn được cung cấp.
2. Không có thông tin -> trả lời đúng câu: "Không tìm thấy thông tin trong kho dữ liệu."
3. Mỗi ý trích dẫn PHẢI ghi nguồn: [số ký hiệu - ngày]. Không bịa.
4. Không suy đoán, không thêm kiến thức ngoài.

CÁC ĐOẠN LIÊN QUAN:
{context}"""


def retrieve(query: str, top_k: int = 8, rerank_pool: int = 24,
             loai_vb: str | None = None, huong: str | None = None):
    qv = embedder.encode_one(query)

    conds = []
    if loai_vb:
        conds.append(qm.FieldCondition(key="loai_vb", match=qm.MatchValue(value=loai_vb)))
    if huong:
        conds.append(qm.FieldCondition(key="huong", match=qm.MatchValue(value=huong)))
    flt = qm.Filter(must=conds) if conds else None

    hits = store.hybrid_query(qv["dense"], qv["sparse"], top_k=rerank_pool, flt=flt)
    if not hits:
        return []

    # rerank cross-encoder trên pool rồi lấy top_k
    passages = [h.payload.get("text", "") for h in hits]
    scores = embedder.rerank(query, passages)
    ranked = sorted(zip(hits, scores), key=lambda x: x[1], reverse=True)[:top_k]
    return [{"text": h.payload.get("text", ""), "payload": h.payload, "score": s}
            for h, s in ranked]


def answer_stream(query: str, **kw):
    ctx_items = retrieve(query, **kw)
    if not ctx_items:
        def _empty():
            yield "[SOURCES][][/SOURCES]"
            yield "Không tìm thấy thông tin trong kho dữ liệu."
        return _empty()

    context = ""
    sources = []
    for i, it in enumerate(ctx_items):
        p = it["payload"]
        tag = f"{p.get('so_ky_hieu','?')} - {p.get('ngay_ban_hanh','?')}"
        context += f"\n[Đoạn {i+1} | {tag}] {it['text']}\n"
        sources.append({"text": it["text"], "metadata": p, "score": it["score"]})

    import json
    def _gen():
        yield f"[SOURCES]{json.dumps(sources, ensure_ascii=False)}[/SOURCES]"
        stream = llm.chat(_SYS.format(context=context), f"CÂU HỎI: {query}",
                          model=config.LLM_MAIN, stream=True)
        for ch in stream:
            if ch.choices[0].delta.content:
                yield ch.choices[0].delta.content
    return _gen()
