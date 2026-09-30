import os
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

from src.core import config, embedder
from src.services import ingest
from src.utils import extract


def workbook(path: str, data_rows: int = 30, stray_row: int | None = None) -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "PL01"
    sheet.append(["BIỂU TỔNG HỢP TẠM ỨNG"])                        # tên biểu
    sheet.append(["STT", "Dự án", "Số dư (đồng)", None])             # tiêu đề cột (ô trống cuối bị bỏ)
    for number in range(1, data_rows + 1):
        sheet.append([number, f"Dự án {number}", 1_500_000.0 * number])
        sheet.append([])                                            # dòng trống xen giữa bị bỏ qua
    if stray_row:
        sheet.cell(row=stray_row, column=2, value="ô lạc cuối sheet")
    book.save(path)


class ExcelExtractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "phu_bieu.xlsx")

    def tearDown(self):
        self.directory.cleanup()

    def test_rows_title_header_and_numbers(self):
        workbook(self.path, data_rows=45)
        text, method = extract.extract_excel(self.path)
        self.assertEqual("xlsx", method)
        self.assertIn("Sheet: PL01 — BIỂU TỔNG HỢP TẠM ỨNG", text)
        self.assertIn("cột: STT | Dự án | Số dư (đồng)", text)
        self.assertIn("| Dự án 3 | 4500000", text)      # số nguyên không có ".0"
        self.assertNotIn("Dòng 4: \n", text)             # dòng trống không sinh chữ
        # Tên cột nhắc lại theo nhóm (45 dòng / 20 = 3 lần), không lặp ở mọi dòng.
        self.assertEqual(3, text.count("; cột: "))
        self.assertEqual(45, text.count("\nDòng "))

    def test_stops_at_long_empty_region(self):
        workbook(self.path, data_rows=10, stray_row=10 + 2 * 10 + config.EXCEL_EMPTY_ROW_STOP + 50)
        text, _ = extract.extract_excel(self.path)
        self.assertIn("Dự án 10", text)
        self.assertNotIn("ô lạc cuối sheet", text)

    def test_keeps_data_after_short_gap(self):
        workbook(self.path, data_rows=5, stray_row=40)
        text, _ = extract.extract_excel(self.path)
        self.assertIn("Dòng 40:  | ô lạc cuối sheet", text)  # ô đầu trống giữ vị trí cột


class LimitTests(unittest.TestCase):
    def test_truncate_adds_note(self):
        text = "\n".join("dòng %d" % i for i in range(20_000))
        cut, note = ingest._truncate(text, 50_000, "Tệp x.xlsx")
        self.assertIsNotNone(note)
        self.assertLess(len(cut), 50_300)
        self.assertIn("[[ĐÃ CẮT BỚT:", cut)
        self.assertEqual(("ngắn", None), ingest._truncate("ngắn", 50_000, "Tệp y"))


class DeviceTests(unittest.TestCase):
    def tearDown(self):
        embedder.device.cache_clear()

    def check(self, setting, cuda_available, expected):
        embedder.device.cache_clear()
        with patch.object(config, "EMBED_DEVICE", setting), \
                patch.object(embedder.torch.cuda, "is_available", return_value=cuda_available):
            self.assertEqual(expected, embedder.device())

    def test_gpu_when_available(self):
        self.check("cuda", True, "cuda")
        self.check("auto", True, "cuda")

    def test_falls_back_to_cpu_without_gpu(self):
        self.check("cuda", False, "cpu")
        self.check("auto", False, "cpu")
        self.check("cpu", True, "cpu")


if __name__ == "__main__":
    unittest.main()
