"""Kiểm tra thể thức và chính tả tệp .docx — điều phối các bước và dựng báo cáo.

Luồng sự kiện (SSE) cho giao diện:
  status → report (thể thức + chính tả bằng code, có ngay) → ai_status → ai (chính tả AI) → done
Không sửa tệp Word; tệp tạm bị xoá sau khi kiểm tra.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from src.check import docx_rules, spelling, spelling_ai
from src.check.docx_model import DocxError, read_docx
from src.check.docx_structure import analyze
from src.core import app_settings

logger = logging.getLogger(__name__)

CUSTOM_WORDS_KEY = "check.custom_words"
_SPELL_LABELS = {"spelling": "Chính tả", "repeat": "Lặp từ", "space": "Khoảng trắng", "punct": "Dấu câu",
                 "bracket": "Dấu ngoặc", "unicode": "Mã ký tự", "ai": "AI"}
# Phạm vi đã được xác minh bằng code so với Phụ lục I NĐ 30; các mục còn lại cần xem thủ công.
COVERAGE = {
    "checked": [
        "Khổ giấy A4, lề trên/dưới/trái/phải (từng section, gộp theo mẫu bố cục)",
        "Phông Times New Roman toàn văn bản, màu chữ đen",
        "Quốc hiệu, tiêu ngữ (nội dung, cỡ chữ, đậm, in hoa)",
        "Tên cơ quan ban hành, dòng số ký hiệu, địa danh ngày tháng, tên loại, trích yếu, V/v",
        "Số, ký hiệu: dấu “:”, số 0 trước số < 10, ký hiệu 27 loại văn bản + 3 loại bản sao (Phụ lục III), mã cơ quan, mã đơn vị",
        "Ngày tháng: số 0 trước ngày < 10 và tháng 1, 2; tháng/năm so với hiện tại",
        "Nội dung: cỡ chữ, căn đều hai lề, thụt đầu dòng, thụt lề trái/phải, khoảng cách đoạn, giãn dòng",
        "Dòng dẫn chiếu phụ lục căn giữa; dòng Kính gửi",
        "Kết thúc nội dung bằng ./.",
        "Nơi nhận (chữ “Nơi nhận” nghiêng đậm, danh sách), chức vụ và họ tên người ký",
        "Số trang: canh giữa lề trên, cỡ chữ, không hiện ở trang đầu",
    ],
    "manual": [
        "Đường kẻ ngang dưới tiêu ngữ và dưới tên cơ quan (là hình vẽ, không đọc được bằng code)",
        "Vị trí các ô trong bố cục trang (Mẫu 1.1 Phụ lục I)",
        "Dấu chỉ mức độ mật, khẩn; chữ ký, con dấu, chữ ký số",
        "Chữ viết tắt tên loại và cơ quan theo danh mục riêng của từng cơ quan (trừ SKHCN đã có danh mục)",
        "Nội dung, căn cứ pháp lý (dùng tab “Nội dung & căn cứ”)",
    ],
}


def _custom_words() -> set[str]:
    words = app_settings.get(CUSTOM_WORDS_KEY) or []
    return {str(word).strip().lower() for word in words if str(word).strip()}


def _spelling_issues(document, structure, custom_words: set[str]) -> list[dict]:
    issues = []
    appendix = structure.appendix_start
    for position, p in enumerate(document.paragraphs):
        if p.empty or p.role in ("quoc_hieu", "tieu_ngu", "so_ky_hieu"):
            continue
        in_appendix = appendix is not None and position >= appendix
        issues.extend(spelling.check_paragraph(p, custom_words, strict_only=in_appendix))
    # Unicode tổ hợp thường do bộ gõ, xuất hiện ở nhiều đoạn: gộp thành một dòng cho cả tài liệu.
    combining = [issue for issue in issues if issue["category"] == "unicode" and "tổ hợp" in issue["message"]]
    if combining:
        issues = [issue for issue in issues if issue not in combining]
        first = combining[0]
        indexes = [issue["index"] for issue in combining]
        issues.insert(0, {**first, "paragraphs": indexes,
                          "message": f"{len(indexes)} đoạn lưu chữ có dấu ở dạng Unicode tổ hợp (dấu tách rời). "
                                     "Nghị định 30 yêu cầu Unicode dựng sẵn (TCVN 6909:2001): đặt bộ gõ ở chế độ "
                                     "“Unicode dựng sẵn” rồi gõ lại, hoặc dùng công cụ chuyển mã của bộ gõ."})
    for issue in issues:
        issue["category_label"] = _SPELL_LABELS.get(issue["category"], issue["category"])
    return issues


def _summary(groups: list[dict], spelling_issues: list[dict]) -> dict:
    fail = warn = passed = 0
    paragraphs = set()
    for group in groups:
        for check in group["checks"]:
            if check.get("hidden"):
                continue
            if check["status"] == "fail":
                fail += 1
            elif check["status"] == "warn":
                warn += 1
            elif check["status"] == "pass":
                passed += 1
            if check["status"] in ("fail", "warn"):
                paragraphs.update(item["index"] for item in check["items"])
    spell_fail = sum(1 for issue in spelling_issues if issue["severity"] == "fail")
    spell_warn = len(spelling_issues) - spell_fail
    status = "fail" if fail or spell_fail else ("warn" if warn or spell_warn else "pass")
    return {"status": status, "fail": fail, "warn": warn, "pass": passed, "paragraphs": len(paragraphs),
            "spelling_fail": spell_fail, "spelling_warn": spell_warn}


def run_format(path: str, profile_key: str, custom: dict | None = None, filename: str | None = None,
               today: dt.date | None = None):
    """Kiểm tra bằng code. Trả (báo cáo, document, structure, lỗi chính tả code)."""
    started = time.perf_counter()
    profile = docx_rules.get_profile(profile_key, custom)
    document = read_docx(path)
    structure = analyze(document)
    report = docx_rules.Report()
    patterns = docx_rules.check_layout(document, profile, report)
    docx_rules.check_page_number(document, profile, report)
    docx_rules.check_fonts(document, profile, report)
    if structure.start is not None:
        # Văn bản của tổ chức Đảng trình bày theo Hướng dẫn 36-HD/VPTW: không áp quy tắc phần đầu,
        # số ký hiệu, nơi nhận/chữ ký của Nghị định 30; vẫn kiểm tra trang, phông chữ và phần nội dung.
        if not structure.party:
            docx_rules.check_header(document, structure, profile, report)
            docx_rules.check_closing(document, structure, profile, report)
        docx_rules.check_reference(document, structure, profile, report, today)
        docx_rules.check_body(document, structure, profile, report)
        docx_rules.check_end_mark(document, structure, profile, report)
    groups = report.result()
    order = ["layout", "header", "reference", "body", "closing", "font"]
    groups.sort(key=lambda g: order.index(g["key"]) if g["key"] in order else 99)
    custom_words = _custom_words()
    spelling_issues = _spelling_issues(document, structure, custom_words)
    body_count = sum(1 for p in document.paragraphs if p.role in ("body", "list", "heading"))
    result = {
        "file": filename,
        "profile": {"key": profile["key"], "label": profile.get("label"), "description": profile.get("description")},
        "structure": {**structure.describe(document.paragraphs), "body_paragraphs": body_count},
        "stats": {"paragraphs": len(document.paragraphs), "nonempty": len(document.nonempty()),
                  "sections": len(document.sections), "tables": document.table_count},
        "layout_patterns": patterns,
        "groups": groups,
        "spelling": {"issues": spelling_issues[:500], "total": len(spelling_issues)},
        "summary": _summary(groups, spelling_issues),
        "seconds": round(time.perf_counter() - started, 2),
    }
    return result, document, structure, spelling_issues


def events(path: str, profile_key: str, custom: dict | None, use_ai: bool, filename: str | None):
    """Sinh sự kiện cho SSE; luôn xoá tệp tạm khi kết thúc."""
    from src.utils.uploads import remove_quietly
    try:
        yield {"type": "status", "message": "Đang đọc tệp và nhận diện cấu trúc văn bản…"}
        try:
            report, document, structure, code_issues = run_format(path, profile_key, custom, filename)
        except (DocxError, docx_rules.ProfileError) as exc:
            yield {"type": "error", "message": str(exc)}
            return
        yield {"type": "report", "data": report}
        if not use_ai:
            yield {"type": "ai", "data": {"status": "off", "message": "Không bật kiểm tra chính tả bằng AI.",
                                          "issues": []}}
        else:
            yield {"type": "ai_status", "message": "Đang kiểm tra chính tả bằng AI…",
                   "paragraphs": len(spelling_ai.scope(document, structure))}
            try:
                ai = spelling_ai.check(document, structure, code_issues, sorted(_custom_words()))
            except Exception as exc:  # AI lỗi không làm hỏng kết quả thể thức
                logger.exception("Chính tả AI thất bại")
                ai = {"status": "error", "message": f"Không kiểm tra được bằng AI: {type(exc).__name__}", "issues": []}
            yield {"type": "ai", "data": ai}
        yield {"type": "done"}
    finally:
        remove_quietly(path)
