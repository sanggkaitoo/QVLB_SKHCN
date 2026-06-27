"""Kiểm tra NỘI DUNG văn bản tải lên:
  A. Căn cứ: trích các "Căn cứ ...", đối chiếu corpus xem văn bản dẫn chiếu có
     tồn tại không (cảnh báo nếu không thấy / có thể đã hết hiệu lực).
  B. LLM: chính tả, lỗi logic, mâu thuẫn — GROUNDED trên ngữ cảnh pháp lý lấy
     từ kho (RAG), bắt buộc dẫn nguồn, không bịa.
"""
import re
from .. import extract, store, search, llm, config

# "Căn cứ <Loại> số 57-NQ/TW ngày 22/12/2024 ..."
_CANCU_RE = re.compile(r"Căn cứ[^;\n]*", re.IGNORECASE)
_SOKY_RE = re.compile(r"số\s*([0-9]+[/-][0-9A-ZĐa-zđ\.\-]+)", re.IGNORECASE)

_SYS = """Bạn là chuyên viên thẩm định văn bản của Sở KH&CN. Rà soát DỰ THẢO dưới đây.
CHỈ báo lỗi có thật, không bịa. Nếu dùng ngữ cảnh pháp lý kèm theo, ghi rõ nguồn [số ký hiệu].
Phân loại lỗi: chinh_ta | logic | can_cu | the_thuc_noi_dung."""

_FMT = """Trả JSON:
{"loi": [{"loai": "...", "trich_doan": "đoạn có vấn đề", "van_de": "mô tả",
          "de_xuat": "cách sửa", "nguon": "[số ký hiệu] nếu có"}],
 "nhan_xet_chung": "1-2 câu"}"""


def _get_text(path: str) -> str:
    text, _ = extract.extract(path)
    return text


def check_can_cu(text: str) -> list[dict]:
    out = []
    for line in _CANCU_RE.findall(text):
        m = _SOKY_RE.search(line)
        if not m:
            continue
        soky = m.group(1)
        hits = store.find_doc_by_soky(soky)
        out.append({
            "can_cu": line.strip()[:160],
            "so_ky_hieu_trich": soky,
            "tim_thay_trong_kho": bool(hits),
            "goi_y": [{"so_ky_hieu": h["so_ky_hieu"],
                       "ngay": str(h["ngay_ban_hanh"]),
                       "trich_yeu": h["trich_yeu"]} for h in hits],
        })
    return out


def check_content(docx_path: str) -> dict:
    text = _get_text(docx_path)
    if not text:
        return {"error": "Không đọc được nội dung file."}

    # A. căn cứ
    cancu = check_can_cu(text)

    # B. ngữ cảnh pháp lý từ kho (RAG) để soi nội dung
    probe = (re.sub(r"\s+", " ", text))[:600]
    ctx_items = search.retrieve(probe, top_k=6, rerank_pool=20)
    legal_ctx = "\n".join(
        f"[{c['payload'].get('so_ky_hieu','?')}] {c['text'][:500]}" for c in ctx_items)

    user = (f"DỰ THẢO CẦN RÀ SOÁT:\n{text[:12000]}\n\n"
            f"NGỮ CẢNH PHÁP LÝ THAM CHIẾU (từ kho văn bản):\n{legal_ctx}\n\n{_FMT}")
    review = llm.extract_json(_SYS, user, model=config.LLM_SMART) or {
        "loi": [], "nhan_xet_chung": "Không phân tích được."}

    return {
        "file": docx_path,
        "kiem_tra_can_cu": cancu,
        "ra_soat_noi_dung": review,
    }
