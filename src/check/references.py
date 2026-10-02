"""Nhận diện và đối chiếu dẫn chiếu văn bản trong dự thảo (bằng code, không dùng AI).

- Dẫn chiếu ở dòng "Căn cứ…" và trong thân văn bản ("Thực hiện Công văn số 123/UBND-NC ngày…"),
  nhiều văn bản trong một dòng, luật dẫn theo tên + ngày (không có số).
- Phân loại: văn bản quy phạm (có năm trong số: 30/2020/NĐ-CP), văn bản hành chính (215/KH-UBND),
  văn bản của Đảng (57-NQ/TW).
- Đối chiếu kho: số ký hiệu, ngày ban hành, cơ quan, trích yếu; quan hệ thay thế/sửa đổi/bãi bỏ trong kho.
- Đối chiếu CSDL quốc gia về pháp luật (vbpl.vn): tình trạng hiệu lực, ngày ban hành của văn bản quy phạm.
- Thể thức khối căn cứ: thứ tự theo hiệu lực pháp lý và thời gian; dấu ";" cuối mỗi dòng, "." dòng cuối.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass, field

from src.check.docx_model import fold
from src.core import store
from src.services.document_fields import normalize_document_ref

TYPE_WORDS = ["Hiến pháp", "Bộ luật", "Luật", "Pháp lệnh", "Lệnh", "Nghị quyết liên tịch", "Nghị quyết", "Nghị định",
              "Thông tư liên tịch", "Thông tư", "Quyết định", "Chỉ thị", "Kế hoạch", "Công văn", "Văn bản",
              "Thông báo", "Hướng dẫn", "Kết luận", "Chương trình", "Đề án", "Báo cáo", "Tờ trình", "Công điện",
              "Quy định", "Quy chế", "Thông tri", "Giấy mời"]
_TYPE_RE = re.compile(r"\b(" + "|".join(sorted(set(TYPE_WORDS), key=len, reverse=True)) + r")\b", re.IGNORECASE)
_ID_RE = re.compile(
    r"(?<![\w/.])("
    r"\d{1,5}(?:\.\d{1,3})?[a-zA-Z]?/\d{4}/[A-ZĐ][A-Za-zĐđ0-9]*(?:[-–][A-ZĐa-zđ][A-Za-zĐđ0-9.]*)*"      # 30/2020/NĐ-CP
    r"|\d{1,5}[a-zA-Z]?/[A-ZĐ][A-Za-zĐđ0-9]*(?:[-–][A-ZĐ][A-Za-zĐđ0-9.]*)*"               # 215/KH-UBND
    r"|\d{1,5}-[A-ZĐ][A-Za-zĐđ]*/[A-ZĐ][A-Za-zĐđ]*)")                                    # 57-NQ/TW
DATE_PATTERN = r"ngày\s*(\d{1,2})\s*(?:tháng\s*|[/.-]\s*)(\d{1,2})\s*(?:năm\s*|[/.-]\s*)(\d{4})"
_DATE_AFTER_ID = re.compile(r"^[\s,]*(?:\(\s*)?(?:ban hành\s+|được ban hành\s+)?" + DATE_PATTERN, re.IGNORECASE)
# Luật dẫn theo tên, không có số: "Luật Tổ chức chính quyền địa phương ngày 16 tháng 6 năm 2025".
_UPPER = "A-ZĐÂĂÊÔƠƯÁÀẢÃẠẤẦẨẪẬẮẰẲẴẶÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ"
_NAMED_LAW_RE = re.compile(r"(?:^|[\s(;,])(Hiến pháp|Bộ luật|Luật|Pháp lệnh)\s+([" + _UPPER + r"]"
                           r"[^;:\n()]{2,140}?)\s*,?\s*(?:được Quốc hội thông qua\s+)?" + DATE_PATTERN)
_ISSUER_STOP = re.compile(r"\s+(về|về việc|việc|v/v|quy định|hướng dẫn|ban hành|phê duyệt|thực hiện|triển khai|"
                          r"đề nghị|giao|tại|theo|ngày|xử lý)\s+|\s+và\s+(?:các\s+)?(?=(?:" + "|".join(TYPE_WORDS) +
                          r")\b)|[,(.;:]", re.IGNORECASE)
_NEXT_DOC = re.compile(r"[,;]?\s+(?:và|cùng|,)\s+(?:các\s+)?(?=(?:" + "|".join(TYPE_WORDS) + r")\s+(?:số\s*)?\d)")
_TITLE_LEAD = re.compile(r"^\s*(về việc|về|v/v|việc)\s+", re.IGNORECASE)
_ABBREV = {"ubnd": "uy ban nhan dan", "hdnd": "hoi dong nhan dan", "ttg": "thu tuong chinh phu",
           "skhcn": "so khoa hoc va cong nghe", "bkhcn": "bo khoa hoc va cong nghe", "khcn": "khoa hoc va cong nghe",
           "kh&cn": "khoa hoc va cong nghe", "tw": "trung uong", "cp": "chinh phu"}
_BASIS_HEADS = ("can cu", "theo de nghi", "xet de nghi")
_SKIP_ROLES = {"quoc_hieu", "tieu_ngu", "co_quan", "so_ky_hieu", "dia_danh", "noi_nhan_title", "noi_nhan_item",
               "chuc_vu", "ky_quyen", "ky_ten", "ky_ghi_chu", "closing_other"}
RANK_LABELS = {0: "Hiến pháp", 1: "Luật, Bộ luật, Nghị quyết của Quốc hội", 2: "Pháp lệnh, Nghị quyết UBTVQH",
               3: "Nghị định, Nghị quyết của Chính phủ", 4: "Quyết định của Thủ tướng Chính phủ",
               5: "Thông tư, Thông tư liên tịch", 6: "Nghị quyết HĐND tỉnh", 7: "Quyết định UBND tỉnh (quy phạm)",
               8: "Văn bản hành chính, văn bản của Đảng"}
KIND_LABELS = {"qppl": "Văn bản quy phạm pháp luật", "ten_luat": "Luật (dẫn theo tên)",
               "hanh_chinh": "Văn bản hành chính", "dang": "Văn bản của Đảng"}


@dataclass
class Reference:
    id: int
    paragraph: int                 # số thứ tự đoạn (Paragraph.index) lần nhắc đầu tiên
    excerpt: str
    snippet: str                   # cụm dẫn chiếu nguyên văn
    doc_type: str | None
    identifier: str | None
    kind: str                      # qppl | hanh_chinh | dang | ten_luat
    scope: str                     # central | local | party
    rank: int
    date: dt.date | None = None
    date_text: str | None = None
    issuer: str | None = None
    title: str | None = None
    in_basis: bool = False         # được nhắc trong dòng "Căn cứ…"
    offset: int = 0                # vị trí trong đoạn (sắp thứ tự các văn bản trên cùng một dòng)
    mentions: list[int] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    kho: dict | None = None
    legal: dict | None = None

    @property
    def label(self) -> str:
        if self.identifier:
            return f"{self.doc_type or 'Văn bản'} {self.identifier}"
        return f"{self.doc_type} {self.title or ''}".strip()

    def add(self, rule: str, label: str, status: str, expected: str | None = None, current: str | None = None,
            hint: str | None = None, link: str | None = None) -> None:
        self.checks.append({"rule": rule, "label": label, "status": status, "expected": expected,
                            "current": current, "hint": hint, "link": link})

    def status(self) -> str:
        statuses = {check["status"] for check in self.checks}
        for status in ("fail", "warn", "pass"):
            if status in statuses:
                return status
        return "info"

    def public(self) -> dict:
        return {"id": self.id, "paragraph": self.paragraph, "excerpt": self.excerpt, "snippet": self.snippet,
                "label": self.label, "doc_type": self.doc_type, "identifier": self.identifier, "kind": self.kind,
                "kind_label": KIND_LABELS[self.kind], "scope": self.scope, "rank": self.rank,
                "rank_label": RANK_LABELS[self.rank], "date": fmt_date(self.date) if self.date else None,
                "issuer": self.issuer, "title": self.title, "in_basis": self.in_basis, "mentions": self.mentions,
                "status": self.status(), "checks": self.checks, "kho": self.kho, "legal": self.legal}


# ------------------------------------------------------------------ nhận diện

def classify(identifier: str | None, doc_type: str | None) -> tuple[str, str, int]:
    """(loại, phạm vi, bậc hiệu lực) của một dẫn chiếu."""
    if not identifier:
        kind_type = fold(doc_type or "")
        return "ten_luat", "central", {"hien phap": 0, "luat": 1, "bo luat": 1}.get(kind_type, 2)
    upper = normalize_document_ref(identifier)
    if re.match(r"^\d+-[A-Z]+/[A-Z]+$", upper) and not re.search(r"HDND|UBND", upper):
        return "dang", "party", 8
    match = re.match(r"^\d+(?:\.\d+)?[A-Z]?/\d{4}/(.+)$", upper)
    if match and ("-" in match.group(1) or re.fullmatch(r"QH\d+", match.group(1))):
        code = match.group(1)
        if "UBTVQH" in code:
            return "qppl", "central", 2
        if re.search(r"QH\d*", code):
            return "qppl", "central", 1
        if re.search(r"(^|-)(ND|NQ)-CP", code):
            return "qppl", "central", 3
        if "TTG" in code:
            return "qppl", "central", 4
        if "HDND" in code:
            return "qppl", "local", 6
        if "UBND" in code:
            return "qppl", "local", 7
        return "qppl", "central", 5
    scope = "local" if re.search(r"UBND|HDND|VPUB|SKHCN|(^|[/-])S[A-Z]{2,}|(^|[/-])TU$|CAT", upper) else "central"
    return "hanh_chinh", scope, 8


def _date(day, month, year) -> dt.date | None:
    try:
        return dt.date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def _tail_fields(tail: str) -> tuple[re.Match | None, str | None, str | None]:
    """Ngày, cơ quan ban hành, trích yếu ngay sau số ký hiệu."""
    tail = re.split(r"[;\n]|(?<=[\w)])\.\s", tail, maxsplit=1)[0][:300]
    date = _DATE_AFTER_ID.match(tail)
    rest = tail[date.end():] if date else tail
    rest = re.sub(r"^\s*\)", "", rest)
    issuer, title = None, None
    issuer_match = re.match(r"\s*,?\s*(?:của|do)\s+(.+)", rest)
    if issuer_match:
        body = issuer_match.group(1)
        stop = _ISSUER_STOP.search(body)
        issuer = (body[:stop.start()] if stop else body).strip(" ,.")[:90] or None
        rest = body[stop.start():] if stop else ""
    lead = _TITLE_LEAD.match(rest.lstrip(" ,")) or re.match(r"^\s*,?\s*(quy định|hướng dẫn|ban hành|phê duyệt)\s",
                                                           rest, re.IGNORECASE)
    if lead:
        title = _TITLE_LEAD.sub("", rest.lstrip(" ,")).strip(" ,.")
        title = re.split(r"\s*\(|;", title)[0]
        # Trích yếu dừng trước văn bản được dẫn tiếp theo trên cùng dòng: "… về công tác văn thư và Nghị quyết số 57…".
        title = _NEXT_DOC.split(title, maxsplit=1)[0].strip(" ,.")[:220] or None
    return date, issuer, title


def is_basis_line(text: str) -> bool:
    return fold(text).startswith(_BASIS_HEADS)


def extract(paragraphs: list[tuple[int, str, str]]) -> list[Reference]:
    """paragraphs: [(số đoạn, văn bản, vai trò)]. Bỏ phần đầu văn bản, nơi nhận, chữ ký. Gộp các lần nhắc lại."""
    found: list[Reference] = []
    for number, text, role in paragraphs:
        if role in _SKIP_ROLES or not text.strip():
            continue
        in_basis = fold(text).startswith("can cu")
        excerpt = " ".join(text.split())[:120]
        spans: list[tuple[int, int]] = []
        for match in _ID_RE.finditer(text):
            identifier = match.group(1).replace("–", "-").rstrip(".")
            window_start = max(0, match.start() - 170)
            before = text[window_start:match.start()]
            types = list(_TYPE_RE.finditer(before))
            has_so = re.search(r"\bsố\s*:?\s*$", before, re.IGNORECASE)
            # Loại văn bản phải đứng gần số ký hiệu (cho phép tên luật xen giữa).
            type_match = types[-1] if types else None
            gap = before[type_match.end():] if type_match else ""
            if type_match and len(gap) > 110:
                type_match, gap = None, ""
            if not type_match and not has_so:
                continue
            doc_type = type_match.group(1)[0].upper() + type_match.group(1)[1:].lower() if type_match else None
            name = re.sub(r"\s*số\s*:?\s*$", "", gap, flags=re.IGNORECASE).strip(" ,")
            date, issuer, title = _tail_fields(text[match.end():])
            kind, scope, rank = classify(identifier, doc_type)
            if doc_type and fold(doc_type) in ("luat", "bo luat", "phap lenh") and len(name) > 3:
                title = name
            start = window_start + type_match.start() if type_match else match.start()
            end = match.end() + (date.end() if date else 0)
            found.append(Reference(
                id=0, paragraph=number, excerpt=excerpt, snippet=" ".join(text[start:end].split())[:240],
                doc_type=doc_type, identifier=identifier, kind=kind, scope=scope, rank=rank,
                date=_date(*date.groups()[-3:]) if date else None,
                date_text=date.group(0).strip(" ,(") if date else None,
                issuer=issuer, title=title, in_basis=in_basis, offset=start, mentions=[number]))
            spans.append((start, end))
        for match in _NAMED_LAW_RE.finditer(text):
            begin = match.start(1)
            if any(a <= begin < b for a, b in spans) or _ID_RE.search(match.group(2)):
                continue
            title = " ".join(match.group(2).split()).strip(" ,")
            if len(title.split()) > 14:
                continue
            doc_type = match.group(1)
            kind, scope, rank = classify(None, doc_type)
            found.append(Reference(
                id=0, paragraph=number, excerpt=excerpt, snippet=" ".join(text[begin:match.end()].split())[:240],
                doc_type=doc_type, identifier=None, kind=kind, scope=scope, rank=rank,
                date=_date(match.group(3), match.group(4), match.group(5)),
                date_text=" ".join(match.group(0)[match.group(0).lower().rfind("ngày"):].split()),
                title=title, in_basis=in_basis, offset=begin, mentions=[number]))
    return _merge(found)


def _key(ref: Reference) -> str:
    if ref.identifier:
        return normalize_document_ref(ref.identifier)
    return f"{fold(ref.doc_type or '')}|{fold(ref.title or '')}"


def _merge(found: list[Reference]) -> list[Reference]:
    """Gộp các lần nhắc cùng một văn bản; báo ngày ghi không thống nhất giữa các lần nhắc."""
    merged: dict[str, Reference] = {}
    for ref in found:
        key = _key(ref)
        current = merged.get(key)
        if current is None:
            merged[key] = ref
            continue
        if ref.paragraph not in current.mentions:
            current.mentions.append(ref.paragraph)
        current.in_basis = current.in_basis or ref.in_basis
        if ref.date and current.date and ref.date != current.date \
                and not any(check["rule"] == "consistency" for check in current.checks):
            current.add("consistency", "Ngày ghi thống nhất trong dự thảo", "fail", fmt_date(current.date),
                        f"{fmt_date(ref.date)} (đoạn {ref.paragraph})",
                        hint="Cùng một văn bản được ghi hai ngày ban hành khác nhau trong dự thảo.")
        for attribute in ("date", "date_text", "issuer", "title", "doc_type"):
            if not getattr(current, attribute) and getattr(ref, attribute):
                setattr(current, attribute, getattr(ref, attribute))
    # Luật vừa dẫn theo tên (có ngày) vừa có số ở chỗ khác: gộp vào bản có số.
    numbered = [ref for ref in merged.values() if ref.kind == "qppl" and ref.rank <= 2 and ref.title]
    for key, ref in list(merged.items()):
        if ref.kind != "ten_luat" or not ref.date:
            continue
        for other in numbered:
            year = int(normalize_document_ref(other.identifier).split("/")[1])
            if fold(other.title) == fold(ref.title) and fold(other.doc_type or "") == fold(ref.doc_type or "") \
                    and year == ref.date.year:
                other.date = other.date or ref.date
                other.date_text = other.date_text or ref.date_text
                other.in_basis = other.in_basis or ref.in_basis
                other.mentions = sorted(set(other.mentions + ref.mentions))
                if (ref.paragraph, ref.offset) < (other.paragraph, other.offset):
                    other.paragraph, other.offset, other.excerpt = ref.paragraph, ref.offset, ref.excerpt
                del merged[key]
                break
    output = sorted(merged.values(), key=lambda ref: (ref.paragraph, ref.offset))
    for position, ref in enumerate(output, 1):
        ref.id = position
    return output


# ------------------------------------------------------------------ đối chiếu

def _words(text: str | None) -> set[str]:
    folded = fold(text or "")
    for short, full in _ABBREV.items():
        folded = re.sub(rf"(?<![a-z]){re.escape(short)}(?![a-z])", full, folded)
    return {word for word in re.findall(r"[a-z0-9]+", folded) if len(word) > 1}


def similarity(a: str | None, b: str | None) -> float:
    left, right = _words(a), _words(b)
    if not left or not right:
        return 1.0
    return len(left & right) / min(len(left), len(right))


def fmt_date(date) -> str:
    if isinstance(date, str):
        try:
            date = dt.date.fromisoformat(date[:10])
        except ValueError:
            return date
    return date.strftime("%d/%m/%Y") if date else "—"


def check_structure(ref: Reference, draft_date: dt.date) -> None:
    if ref.kind == "qppl":
        year = int(normalize_document_ref(ref.identifier).split("/")[1])
        if not 1945 <= year <= draft_date.year:
            ref.add("year", "Năm trong số ký hiệu", "fail", f"1945–{draft_date.year}", str(year))
        if ref.date and ref.date.year != year:
            ref.add("year_date", "Năm ban hành khớp số ký hiệu", "fail", str(year), str(ref.date.year),
                    hint="Số ký hiệu văn bản quy phạm mang năm ban hành; kiểm tra lại ngày hoặc số.")
    if ref.date and ref.date > draft_date:
        ref.add("future", "Ngày văn bản được dẫn", "fail", f"Trước ngày dự thảo ({fmt_date(draft_date)})",
                fmt_date(ref.date), hint="Dẫn chiếu một văn bản có ngày sau ngày của dự thảo.")


def check_kho(ref: Reference) -> None:
    if not ref.identifier:
        return
    rows = store.search_documents_by_reference(ref.identifier, limit=3)
    if not rows:
        target = normalize_document_ref(ref.identifier)
        if ref.kind == "qppl" and ref.scope == "central":
            return  # văn bản Trung ương: đối chiếu trên vbpl.vn
        # Gợi ý nhầm số chỉ khi văn bản gần giống có cùng ngày ban hành (dấu hiệu gõ sai số).
        similar = [row for row in store.find_doc_by_soky(ref.identifier)
                   if ref.date and row.get("ngay_ban_hanh") == ref.date
                   and normalize_document_ref(row["so_ky_hieu"] or "") != target
                   and difflib.SequenceMatcher(None, normalize_document_ref(row["so_ky_hieu"] or ""), target).ratio() >= 0.8]
        if similar:
            ref.add("kho", "Có trong kho văn bản", "warn", "Tìm thấy", "Không thấy số này",
                    hint="Có thể là: " + "; ".join(f"{row['so_ky_hieu']} ngày {fmt_date(row['ngay_ban_hanh'])}"
                                                   for row in similar[:3]))
        elif ref.kind in ("hanh_chinh", "dang"):
            ref.add("kho", "Có trong kho văn bản", "info", "Tìm thấy", "Chưa đối chiếu được",
                    hint="Văn bản không có trong kho đi/đến của Sở; tự kiểm tra lại số ký hiệu và ngày.")
        return
    doc = dict(rows[0])
    ref.kho = {"id": doc["id"], "so_ky_hieu": doc["so_ky_hieu"], "ngay_ban_hanh": fmt_date(doc.get("ngay_ban_hanh")),
               "co_quan": doc.get("co_quan_ban_hanh"), "trich_yeu": doc.get("trich_yeu"), "url": doc.get("source_url"),
               "huong": doc.get("huong"), "loai_vb": doc.get("loai_vb")}
    issued = doc.get("ngay_ban_hanh")
    if ref.date and issued:
        same = ref.date == issued
        ref.add("date", "Ngày ban hành (kho)", "pass" if same else "fail", fmt_date(issued), fmt_date(ref.date),
                hint=None if same else "Ngày ghi trong dự thảo khác ngày của văn bản trong kho.")
    if ref.issuer and doc.get("co_quan_ban_hanh") and similarity(ref.issuer, doc["co_quan_ban_hanh"]) < 0.5:
        ref.add("issuer", "Cơ quan ban hành (kho)", "warn", doc["co_quan_ban_hanh"], ref.issuer)
    if ref.title and doc.get("trich_yeu") and similarity(ref.title, doc["trich_yeu"]) < 0.35:
        ref.add("title", "Trích yếu (kho)", "warn", doc["trich_yeu"][:200], ref.title[:200],
                hint="Trích yếu ghi trong dự thảo khác nhiều so với văn bản trong kho.")
    ref.add("kho", "Có trong kho văn bản", "pass", "Tìm thấy", doc["so_ky_hieu"])
    for relation in store.get_document_relations([doc["id"]]):
        if relation.get("target_document_id") != doc["id"] or relation.get("source_document_id") == doc["id"]:
            continue
        kind = relation.get("relation_type")
        newer = relation.get("source_so_ky_hieu") or "văn bản khác"
        verified = bool(relation.get("verified"))
        note = None if verified else "Quan hệ trích tự động từ nội dung văn bản trong kho, chưa được xác minh."
        if kind in ("thay_the", "bai_bo"):
            ref.add("relation", "Hiệu lực theo kho", "fail" if verified else "warn", "Còn hiệu lực",
                    f"Đã bị {'thay thế' if kind == 'thay_the' else 'bãi bỏ'} bởi {newer}", hint=note)
        elif kind == "sua_doi":
            ref.add("relation", "Hiệu lực theo kho", "warn", "Không bị sửa đổi", f"Được sửa đổi, bổ sung bởi {newer}",
                    hint="Cân nhắc dẫn thêm văn bản sửa đổi, bổ sung." + (f" {note}" if note else ""))


def wants_legal(ref: Reference) -> bool:
    return ref.kind == "qppl" or (ref.kind == "ten_luat" and ref.date is not None and ref.rank >= 1)


def check_legal(ref: Reference, client, draft_issuer: str = "") -> None:
    """Đối chiếu CSDL quốc gia về pháp luật (vbpl.vn): tình trạng hiệu lực, ngày ban hành."""
    from src.check import legal_registry
    if ref.kind == "ten_luat":
        result = legal_registry.lookup_named(ref.doc_type, ref.title or "", ref.date, client=client)
    else:
        result = legal_registry.lookup(ref.identifier, ref.title or "", client=client,
                                       issuer=f"{ref.issuer or ''} {draft_issuer}", issued=ref.date)
    ref.legal = result
    if result["status"] == "unavailable":
        ref.add("legal", "CSDL quốc gia về pháp luật", "info", None, "Chưa đối chiếu được", hint=result.get("message"))
        return
    if result["status"] != "found":
        year = int(normalize_document_ref(ref.identifier).split("/")[1]) if ref.identifier else 0
        if ref.kind == "qppl" and ref.scope == "central" and year and year < dt.date.today().year - 1:
            ref.add("legal", "CSDL quốc gia về pháp luật", "warn", "Có trên vbpl.vn", "Không tìm thấy",
                    hint="Không tìm thấy số ký hiệu này trên vbpl.vn; kiểm tra lại số, năm và ký hiệu.")
        elif ref.kind == "qppl" and ref.scope == "central":
            ref.add("legal", "CSDL quốc gia về pháp luật", "info", "Có trên vbpl.vn", "Chưa tìm thấy",
                    hint="Văn bản mới có thể chưa được cập nhật lên vbpl.vn; tự kiểm tra lại số, năm và ký hiệu.")
        else:
            ref.add("legal", "CSDL quốc gia về pháp luật", "info", None, "Chưa đối chiếu được",
                    hint="Văn bản chưa có trên vbpl.vn (văn bản địa phương có thể chưa được cập nhật).")
        return
    level = result["force_level"]
    hint = None
    if level == "fail":
        hint = "Văn bản đã hết hiệu lực; kiểm tra văn bản thay thế trước khi dẫn."
    elif result.get("legal_force") == "PartiallyInForce":
        hint = "Văn bản hết hiệu lực một phần; bảo đảm nội dung được dẫn còn hiệu lực."
    ref.add("force", "Hiệu lực (vbpl.vn)", level, "Còn hiệu lực", result["force_label"], hint=hint, link=result["url"])
    issued = result.get("issued")
    if ref.kind == "ten_luat" and result.get("date_mismatch"):
        ref.add("date", "Ngày thông qua (vbpl.vn)", "fail", fmt_date(issued), fmt_date(ref.date),
                hint=f"{result.get('name')} được thông qua ngày {fmt_date(issued)}.")
    elif ref.date and issued:
        same = ref.date.isoformat() == issued
        if not same or not any(check["rule"] == "date" for check in ref.checks):
            ref.add("date", "Ngày ban hành (vbpl.vn)", "pass" if same else "fail", fmt_date(issued), fmt_date(ref.date))
    if ref.title and result.get("name") and ref.kind == "qppl" and similarity(ref.title, result["name"]) < 0.3:
        ref.add("title", "Trích yếu (vbpl.vn)", "warn", result["name"][:220], ref.title[:220],
                hint="Trích yếu trong dự thảo khác văn bản cùng số trên vbpl.vn; kiểm tra lại số ký hiệu.")
    if result.get("same_number", 1) > 1:
        ref.add("same_number", "Văn bản trùng số trên vbpl.vn", "info", None,
                f"{result['same_number']} văn bản cùng số", hint="Đã chọn văn bản khớp ngày/trích yếu nhất; "
                "lưu ý văn bản của tỉnh trước và sau hợp nhất có thể trùng số.", link=result["url"])
    if ref.doc_type and result.get("doc_type") and fold(ref.doc_type) not in ("van ban",) \
            and fold(result["doc_type"]) != fold(ref.doc_type):
        ref.add("type", "Loại văn bản (vbpl.vn)", "warn", result["doc_type"], ref.doc_type)


def check_basis_block(refs: list[Reference], paragraphs: list[tuple[int, str, str]]) -> list[dict]:
    """Khối "Căn cứ…" liền nhau: dấu câu cuối dòng và thứ tự theo hiệu lực pháp lý / thời gian."""
    lines: list[tuple[int, str]] = []
    for number, text, role in paragraphs:
        if role in _SKIP_ROLES or not text.strip():
            continue
        if is_basis_line(text):
            lines.append((number, text.strip()))
        elif lines:
            break  # khối căn cứ kết thúc ở đoạn đầu tiên không phải căn cứ
    if not lines:
        return []
    results = []
    for position, (number, text) in enumerate(lines):
        last = position == len(lines) - 1
        want = "." if last else ";"
        ok = text.endswith(want) or (last and text.endswith((".", ":", ";")))
        if last and text.endswith(";"):
            ok = False
        if not ok:
            results.append({"rule": "punct", "label": "Dấu cuối dòng căn cứ", "status": "warn",
                            "expected": f"Kết thúc bằng “{want}”", "current": f"“…{text[-14:]}”",
                            "paragraph": number, "excerpt": " ".join(text.split())[:120]})
    basis_numbers = {number for number, _ in lines}
    per_line: dict[int, Reference] = {}
    for ref in sorted(refs, key=lambda r: (r.paragraph, r.offset)):
        if ref.paragraph in basis_numbers and fold(ref.excerpt).startswith("can cu"):
            per_line.setdefault(ref.paragraph, ref)  # văn bản chính của mỗi dòng căn cứ (đứng đầu dòng)
    for ref in per_line.values():
        if ref.identifier and not ref.date:
            results.append({"rule": "basis_date", "label": "Căn cứ ghi đủ ngày ban hành", "status": "warn",
                            "expected": "Loại, số ký hiệu, ngày ban hành, cơ quan, trích yếu",
                            "current": f"{ref.label}: không ghi ngày ban hành",
                            "paragraph": ref.paragraph, "excerpt": ref.excerpt})
    # Luật Tổ chức… (căn cứ thẩm quyền) luôn đứng đầu, không xét thứ tự thời gian.
    ordered = [ref for ref in per_line.values() if not fold(ref.title or "").startswith("to chuc")]
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.rank < prev.rank:
            results.append({"rule": "order", "label": "Thứ tự căn cứ theo hiệu lực pháp lý", "status": "warn",
                            "expected": f"{RANK_LABELS[cur.rank]} đứng trước {RANK_LABELS[prev.rank]}",
                            "current": f"{cur.label} đứng sau {prev.label}",
                            "paragraph": cur.paragraph, "excerpt": cur.excerpt})
        elif cur.rank == prev.rank and cur.rank < 8 and cur.date and prev.date and cur.date < prev.date:
            results.append({"rule": "order_time", "label": "Thứ tự căn cứ cùng cấp theo thời gian", "status": "warn",
                            "expected": "Văn bản cùng cấp ban hành trước đứng trước",
                            "current": f"{cur.label} ({fmt_date(cur.date)}) đứng sau {prev.label} ({fmt_date(prev.date)})",
                            "paragraph": cur.paragraph, "excerpt": cur.excerpt})
    if not results:
        results.append({"rule": "basis_ok", "label": "Khối căn cứ", "status": "pass",
                        "expected": f"{len(lines)} dòng căn cứ: dấu câu và thứ tự hợp lệ", "current": None,
                        "paragraph": lines[0][0], "excerpt": " ".join(lines[0][1].split())[:120]})
    return results
