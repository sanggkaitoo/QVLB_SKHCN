"""Kiểm tra "Nội dung & căn cứ" — điều phối các bước và phát sự kiện cho giao diện (SSE).

Luồng sự kiện:
  status → report (dẫn chiếu + đối chiếu kho + khối căn cứ + logic, bằng code)
         → legal_item… → legal (CSDL quốc gia về pháp luật, từng văn bản)
         → related (văn bản đến có thể đang được trả lời nhưng chưa dẫn chiếu)
         → ai_status → ai (thẩm định bằng AI) → done
Không sửa tệp; tệp tạm bị xoá khi kết thúc.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from src.check import content_ai, content_logic, legal_registry, references
from src.check.docx_model import DocxError, fold, read_docx
from src.check.docx_structure import analyze
from src.core import config, store
from src.services.document_fields import normalize_document_ref

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})", re.IGNORECASE)
_NUMBER_RE = re.compile(r"Số\s*:?\s*([0-9]*\s*/[^\s,]+)", re.IGNORECASE)
COVERAGE = {
    "code": [
        "Nhận diện văn bản được dẫn trong căn cứ và thân văn bản (nhiều văn bản trên một dòng, luật dẫn theo tên)",
        "Đối chiếu kho: số ký hiệu, ngày ban hành, cơ quan ban hành, trích yếu; văn bản đã bị thay thế, bãi bỏ, sửa đổi",
        "CSDL quốc gia về pháp luật (vbpl.vn): tình trạng hiệu lực, ngày ban hành văn bản quy phạm Trung ương và Lào Cai",
        "Khối căn cứ: thứ tự theo hiệu lực pháp lý và thời gian, dấu “;” cuối dòng và “.” dòng cuối",
        "Ngày tháng không tồn tại, thứ trong tuần không khớp, thời hạn đã qua, khoảng thời gian ngược",
        "Số viết bằng số và bằng chữ không khớp; Phụ lục, Điều, đề mục được dẫn nhưng không có hoặc đánh số nhảy",
        "Văn bản đến liên quan trong kho mà dự thảo chưa dẫn chiếu",
    ],
    "ai": [
        "Đối chiếu nội dung dự thảo với nguyên văn văn bản được dẫn (thời hạn, số liệu, đơn vị, yêu cầu) — có trích dẫn bằng chứng",
        "Logic, số liệu, thẩm quyền, thời hạn, diễn đạt gây hiểu sai (không kiểm tra chính tả, thể thức)",
    ],
    "manual": [
        "Văn bản của Đảng và văn bản hành chính ngoài kho của Sở: chỉ kiểm tra được cấu trúc, cần tự đối chiếu",
        "Sự phù hợp về chủ trương, chính sách; tính khả thi của nhiệm vụ",
    ],
}


@dataclass
class _Line:
    """Đoạn văn của tệp .doc/.pdf (không có định dạng Word)."""
    index: int
    text: str
    role: str = "body"
    in_table: bool = False

    @property
    def empty(self) -> bool:
        return not self.text.strip()

    def excerpt(self, limit: int = 90) -> str:
        text = " ".join(self.text.split())
        return text if len(text) <= limit else text[:limit - 1] + "…"


@dataclass
class _PlainDocument:
    paragraphs: list


def _plain_document(path: str) -> _PlainDocument:
    from src.utils import extract
    text, _ = extract.extract(path)
    lines = [line.strip() for line in (text or "").splitlines()]
    paragraphs, closing = [], False
    for position, line in enumerate(lines):
        if not line:
            continue
        folded = fold(line)
        role = "body"
        if folded.startswith("noi nhan"):
            closing = True
        if closing:
            role = "noi_nhan_item"
        elif position < 25 and folded.startswith("cong hoa xa hoi"):
            role = "quoc_hieu"
        elif position < 25 and folded.startswith("doc lap"):
            role = "tieu_ngu"
        elif position < 25 and _NUMBER_RE.match(line):
            role = "so_ky_hieu"
        elif position < 30 and re.match(r"^[^\d,]{2,60},\s*ngay\b", folded):
            role = "dia_danh"
        paragraphs.append(_Line(index=len(paragraphs) + 1, text=line, role=role))
    if not paragraphs:
        raise DocxError("Không đọc được nội dung tệp.")
    return _PlainDocument(paragraphs)


def _load(path: str):
    if Path(path).suffix.lower() == ".docx":
        try:
            document = read_docx(path)
        except DocxError:
            return _plain_document(path), None  # tệp .doc đổi đuôi: đọc phần chữ
        return document, analyze(document)
    return _plain_document(path), None


def _meta(document, structure) -> dict:
    by_role: dict[str, list[str]] = {}
    for p in document.paragraphs:
        if p.text.strip():
            by_role.setdefault(p.role, []).append(" ".join(p.text.split()))
    number = None
    for text in by_role.get("so_ky_hieu", []):
        match = _NUMBER_RE.search(text)
        if match:
            number = re.sub(r"\s+", "", match.group(1))
            break
    date, date_text = None, None
    for text in by_role.get("dia_danh", []):
        match = _DATE_RE.search(text)
        if match:
            try:
                date = dt.date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
                date_text = date.strftime("%d/%m/%Y")
            except ValueError:
                pass
            break
    issuer = " ".join(by_role.get("co_quan", [])[-2:]) or None
    subject = " ".join(by_role.get("trich_yeu", []) or by_role.get("vv", [])) or None
    doc_type = structure.doc_type_label if structure is not None else None
    return {"number": number, "date": date.isoformat() if date else None, "date_text": date_text,
            "dated": date is not None, "issuer": issuer, "subject": subject, "doc_type": doc_type}


def _summary(refs: list[dict], basis: list[dict], logic: list[dict]) -> dict:
    statuses = [ref["status"] for ref in refs] + [item["status"] for item in basis + logic]
    fail, warn = statuses.count("fail"), statuses.count("warn")
    return {"status": "fail" if fail else ("warn" if warn else "pass"), "fail": fail, "warn": warn,
            "references": len(refs), "in_kho": sum(1 for ref in refs if ref.get("kho")),
            "legal": sum(1 for ref in refs if ref["kind"] in ("qppl", "ten_luat")),
            "basis_lines": len({item["paragraph"] for item in basis}),
            "logic": sum(1 for item in logic if item["status"] in ("fail", "warn"))}


def run_code(path: str, filename: str | None = None, today: dt.date | None = None):
    """Phần kiểm tra bằng code. Trả (báo cáo, document, refs, meta)."""
    started = time.perf_counter()
    document, structure = _load(path)
    meta = _meta(document, structure)
    draft_date = dt.date.fromisoformat(meta["date"]) if meta["date"] else (today or dt.date.today())
    paragraphs = [(p.index, p.text, p.role) for p in document.paragraphs]
    tables = {p.index for p in document.paragraphs if p.in_table}
    own = normalize_document_ref(meta["number"]) if meta["number"] else None
    refs = [ref for ref in references.extract(paragraphs)
            if not (own and ref.identifier and normalize_document_ref(ref.identifier) == own)]
    for ref in refs:
        references.check_structure(ref, draft_date)
        try:
            references.check_kho(ref)
        except Exception as exc:  # kho không truy cập được không làm hỏng các kiểm tra khác
            logger.warning("Đối chiếu kho lỗi: %s", exc)
            ref.add("kho", "Có trong kho văn bản", "info", None, "Chưa đối chiếu được", hint=type(exc).__name__)
    basis = references.check_basis_block(refs, paragraphs)
    logic = content_logic.check(paragraphs, draft_date, meta["dated"], tables)
    public_refs = [ref.public() for ref in refs]
    report = {
        "file": filename,
        "meta": meta,
        "draft_date": draft_date.strftime("%d/%m/%Y"),
        "references": public_refs,
        "basis": basis,
        "logic": logic,
        "summary": _summary(public_refs, basis, logic),
        "coverage": COVERAGE,
        "seconds": round(time.perf_counter() - started, 2),
    }
    return report, document, refs, meta


def related_incoming(document, refs, meta: dict, limit: int = 3) -> list[dict]:
    """Văn bản đến trong kho có nội dung gần với dự thảo nhưng chưa được dẫn chiếu."""
    from src.services import retrieval_srv
    body = [p.text for p in document.paragraphs if p.role in ("body", "list") and len(p.text) > 60][:3]
    query = " ".join(filter(None, [meta.get("subject"), *body]))[:600]
    if len(query) < 40:
        return []
    cited = {normalize_document_ref(ref.identifier) for ref in refs if ref.identifier}
    draft_date = dt.date.fromisoformat(meta["date"]) if meta.get("date") else dt.date.today()
    hits = retrieval_srv.hybrid_search(query, top_k=10, filters={"huong": "den"}, rerank_pool=30, min_score=0.3)
    scores: dict[int, float] = {}
    for hit in hits:
        doc_id = hit["payload"].get("doc_id")
        if doc_id is not None:
            scores[int(doc_id)] = max(scores.get(int(doc_id), 0.0), float(hit.get("score") or 0))
    documents = store.documents_by_ids(scores)
    output = []
    for doc_id, score in sorted(scores.items(), key=lambda item: -item[1]):
        doc = documents.get(doc_id)
        if not doc or not doc.get("so_ky_hieu") or normalize_document_ref(doc["so_ky_hieu"]) in cited:
            continue
        issued = doc.get("ngay_ban_hanh")
        if issued and not (draft_date - dt.timedelta(days=180) <= issued <= draft_date):
            continue
        output.append({"id": doc_id, "so_ky_hieu": doc["so_ky_hieu"], "ngay_ban_hanh": references.fmt_date(issued),
                       "co_quan": doc.get("co_quan_ban_hanh"), "trich_yeu": doc.get("trich_yeu"),
                       "url": doc.get("source_url"), "score": round(score, 3)})
        if len(output) >= limit:
            break
    return output


def events(path: str, filename: str | None, use_ai: bool, use_legal: bool):
    """Sinh sự kiện cho SSE; luôn xoá tệp tạm khi kết thúc."""
    from src.utils.uploads import remove_quietly
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        yield {"type": "status", "message": "Đang đọc tệp, nhận diện văn bản được dẫn và đối chiếu kho…"}
        try:
            report, document, refs, meta = run_code(path, filename)
        except DocxError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        except Exception as exc:
            logger.exception("Kiểm tra nội dung thất bại")
            yield {"type": "error", "message": f"Không đọc được tệp: {type(exc).__name__}"}
            return
        yield {"type": "report", "data": report}

        ai_future = pool.submit(content_ai.check, document, refs, meta) if use_ai else None
        if ai_future is not None:
            yield {"type": "ai_status", "message": "AI đang đối chiếu văn bản được dẫn và rà soát nội dung…"}

        legal_refs = [ref for ref in refs if references.wants_legal(ref)][:config.LEGAL_MAX_LOOKUPS]
        if not use_legal or not config.LEGAL_REGISTRY_ENABLED:
            yield {"type": "legal", "data": {"status": "off", "message": "Không bật đối chiếu CSDL quốc gia về pháp luật."}}
        elif not legal_refs:
            yield {"type": "legal", "data": {"status": "none", "message": "Không có văn bản quy phạm pháp luật cần đối chiếu."}}
        elif not legal_registry.status().get("built_at"):
            legal_registry.build_in_background()
            yield {"type": "legal", "data": {"status": "unavailable",
                                             "message": "Danh mục vbpl.vn đang được tạo lần đầu (khoảng 2 phút); hãy kiểm tra lại sau."}}
        else:
            yield {"type": "legal_status", "message": f"Đang tra cứu {len(legal_refs)} văn bản trên vbpl.vn…",
                   "total": len(legal_refs)}
            started = time.perf_counter()
            with legal_registry.open_client() as client:
                for ref in legal_refs:
                    try:
                        references.check_legal(ref, client, meta.get("issuer") or "")
                    except Exception as exc:
                        logger.warning("Tra cứu vbpl.vn lỗi: %s", exc)
                        ref.add("legal", "CSDL quốc gia về pháp luật", "info", None, "Chưa đối chiếu được",
                                hint=type(exc).__name__)
                    yield {"type": "legal_item", "data": ref.public()}
            found = sum(1 for ref in legal_refs if (ref.legal or {}).get("status") == "found")
            yield {"type": "legal", "data": {"status": "done", "checked": len(legal_refs), "found": found,
                                             "index": legal_registry.status(),
                                             "seconds": round(time.perf_counter() - started, 1)}}

        try:
            related = related_incoming(document, refs, meta)
        except Exception as exc:
            logger.warning("Tìm văn bản đến liên quan lỗi: %s", exc)
            related = []
        yield {"type": "related", "data": related}

        if ai_future is None:
            yield {"type": "ai", "data": {"status": "off", "message": "Không bật thẩm định bằng AI.", "issues": []}}
        else:
            try:
                ai = ai_future.result()
            except Exception as exc:  # AI lỗi không làm hỏng kết quả kiểm tra bằng code
                logger.exception("Thẩm định nội dung bằng AI thất bại")
                ai = {"status": "error", "message": f"Không thẩm định được bằng AI: {type(exc).__name__}", "issues": []}
            yield {"type": "ai", "data": ai}
        yield {"type": "done"}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        remove_quietly(path)
