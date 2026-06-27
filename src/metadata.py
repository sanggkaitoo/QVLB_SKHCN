"""Trích metadata có cấu trúc bằng LLM rẻ (Flash-Lite).
Đầu vào: phần đầu văn bản (header thường nằm ở đây).
Đầu ra: dict các trường để LỌC và TỔNG HỢP về sau.
"""
import re
import datetime as dt
from . import llm, config

# 29 loại văn bản hành chính theo Nghị định 30/2020/NĐ-CP (Điều 7)
# Mã loại dùng viết tắt NĐ30 để gọn + dễ lọc.
LOAI_VB = {
    "nghi_quyet":       "NQ",    # 1.  Nghị quyết (cá biệt)
    "quyet_dinh":       "QĐ",   # 2.  Quyết định (cá biệt)
    "chi_thi":          "CT",    # 3.  Chỉ thị
    "quy_che":          "QC",    # 4.  Quy chế
    "quy_dinh":         "QYĐ",  # 5.  Quy định
    "thong_cao":        "TC",    # 6.  Thông cáo
    "thong_bao":        "TB",    # 7.  Thông báo
    "huong_dan":        "HD",    # 8.  Hướng dẫn
    "chuong_trinh":     "CTr",   # 9.  Chương trình
    "ke_hoach":         "KH",    # 10. Kế hoạch
    "phuong_an":        "PA",    # 11. Phương án
    "de_an":            "ĐA",   # 12. Đề án
    "du_an":            "DA",    # 13. Dự án
    "bao_cao":          "BC",    # 14. Báo cáo
    "bien_ban":         "BB",    # 15. Biên bản
    "to_trinh":         "TTr",   # 16. Tờ trình
    "hop_dong":         "HĐ",   # 17. Hợp đồng
    "cong_van":         "CV",    # 18. Công văn
    "cong_dien":        "CĐ",   # 19. Công điện
    "ban_ghi_nho":      "BGN",   # 20. Bản ghi nhớ
    "ban_thoa_thuan":   "BTT",   # 21. Bản thỏa thuận
    "giay_uy_quyen":    "GUQ",   # 22. Giấy ủy quyền
    "giay_moi":         "GM",    # 23. Giấy mời
    "giay_gioi_thieu":  "GGT",   # 24. Giấy giới thiệu
    "giay_nghi_phep":   "GNP",   # 25. Giấy nghỉ phép
    "phieu_gui":        "PG",    # 26. Phiếu gửi
    "phieu_chuyen":     "PC",    # 27. Phiếu chuyển
    "phieu_bao":        "PB",    # 28. Phiếu báo
    "thu_cong":         "TC2",   # 29. Thư công
    "khac":             "",      # fallback
}
_VALID_LOAI = set(LOAI_VB.keys())

# Bảng tra ngược viết tắt -> mã nội bộ (để suy từ số ký hiệu "215/KH-UBND")
_VIETTAT_MAP = {}
for k, v in LOAI_VB.items():
    if v:
        _VIETTAT_MAP[v] = k

_LOAI_LIST_STR = ", ".join(f"{k} ({v})" for k, v in LOAI_VB.items() if k != "khac")

_SYS = f"""Bạn là chuyên viên văn thư. Trích thông tin định danh từ một văn bản hành chính Việt Nam.
Phân loại loai_vb vào ĐÚNG MỘT trong 29 loại theo NĐ 30/2020/NĐ-CP:
{_LOAI_LIST_STR}, khac.
Nếu không chắc trường nào, để null. Ngày theo định dạng YYYY-MM-DD."""

_FIELDS = """Trả JSON với khóa:
{"so_ky_hieu": str|null, "ngay_ban_hanh": "YYYY-MM-DD"|null, "loai_vb": str,
 "viet_tat_loai": str|null,
 "co_quan_ban_hanh": str|null, "nguoi_ky": str|null, "chuc_vu_nguoi_ky": str|null,
 "trich_yeu": str|null,
 "chu_truong": ["tên nghị quyết/đề án lớn của TW hoặc tỉnh mà VB này phục vụ, VD: Trung ương: Nghị quyết 57-NQ/TW, Nghị quyết 59-NQ/TW, Nghị quyết 66-NQ/TW, Nghị quyết 68-NQ/TW, Nghị quyết 70-NQ/TW, Nghị quyết 71-NQ/TW, Đề án 06/QĐ-TTg, Chiến lược 942/QĐ-TTg, Chương trình 749/QĐ-TTg, Tỉnh: Đề án số 11, Rỗng nếu không rõ"],
 "linh_vuc": ["chọn 1+ trong: khoa_hoc, cong_nghe, dmst, cds, tdđlcl, bcvt"],
 "chuyen_de": ["3-6 từ khóa chuyên đề: hạ tầng số, chính quyền số, kinh tế số, xã hội số, nghiên cứu khoa học, sở hữu trí tuệ, khởi nghiệp, viễn thông, mạng số liệu chuyên dùng, internet, đầu tư, dự án đầu tư công, dự án chi thường xuyên..."]}"""


def guess_loai_from_soky(so_ky_hieu: str | None) -> str | None:
    """Đoán loại VB từ phần viết tắt trong số ký hiệu: '215/KH-UBND' -> 'ke_hoach'."""
    if not so_ky_hieu:
        return None
    import re
    m = re.search(r"[/\-]([A-ZĐa-zđ]+)", so_ky_hieu)
    if m:
        vt = m.group(1).upper()
        return _VIETTAT_MAP.get(vt)
    return None


def extract_metadata(full_text: str, fallback: dict | None = None) -> dict:
    fallback = fallback or {}
    head = full_text[:3000]
    tail = full_text[-1500:]   # người ký thường ở cuối
    user = f"PHẦN ĐẦU:\n{head}\n\nPHẦN CUỐI:\n{tail}\n\n{_FIELDS}"
    data = llm.extract_json(_SYS, user, model=config.LLM_CHEAP) or {}

    # hợp nhất với metadata crawler (.meta.json) làm dự phòng
    for k in ("so_ky_hieu", "ngay_ban_hanh", "trich_yeu"):
        if not data.get(k) and fallback.get(k) and fallback.get(k) != "N/A":
            data[k] = fallback[k]

    data["ngay_ban_hanh"] = _norm_date(data.get("ngay_ban_hanh"))

    # validate loai_vb: LLM -> viết tắt -> số ký hiệu -> khac
    loai = data.get("loai_vb")
    if loai not in _VALID_LOAI:
        vt = data.get("viet_tat_loai")
        loai = _VIETTAT_MAP.get(vt) if vt else None
    if loai not in _VALID_LOAI:
        loai = guess_loai_from_soky(data.get("so_ky_hieu"))
    data["loai_vb"] = loai if loai in _VALID_LOAI else "khac"

    # gán viết tắt chuẩn
    data["viet_tat_loai"] = LOAI_VB.get(data["loai_vb"], "")

    _LV_VALID = {"khoa_hoc","cong_nghe","dmst","cds","tdđlcl","bcvt"}
    data["chu_truong"] = [str(t).strip() for t in (data.get("chu_truong") or []) if t]
    data["linh_vuc"] = [v for v in (data.get("linh_vuc") or []) if v in _LV_VALID]
    data["chuyen_de"] = [str(t).strip() for t in (data.get("chuyen_de") or [])[:8] if t]

    return data



def _norm_date(v):
    if not v or v == "None":
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date().isoformat()
        except Exception:
            continue
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", str(v))
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return dt.date(y, mo, d).isoformat()
        except Exception:
            return None
    return None
