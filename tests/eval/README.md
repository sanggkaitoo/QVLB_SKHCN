# Baseline RAG

Bộ đo này giữ nguyên pipeline trong `src/services/search_srv.py` và thực hiện ba bước:

```bash
./venv/bin/python scripts/generate_eval_set.py --count 30
./venv/bin/python scripts/run_baseline_api.py --judge --overwrite
./venv/bin/python scripts/report_baseline.py
```

Chạy retrieval-only để kiểm tra nhanh, không gọi model trả lời hoặc model chấm:

```bash
./venv/bin/python scripts/run_baseline.py --skip-answer --overwrite
./venv/bin/python scripts/report_baseline.py
```

Các file câu hỏi và báo cáo sinh ra nằm tại `tests/eval/generated_questions.jsonl` và
`reports/generated/`. Chúng không được đưa lên Git vì có thể chứa nội dung văn bản nội bộ.

Trước khi dùng kết quả làm tiêu chí nghiệm thu, chọn ngẫu nhiên ít nhất 15-20 câu để cán bộ
nghiệp vụ xác nhận câu hỏi, đáp án và bằng chứng chuẩn.
