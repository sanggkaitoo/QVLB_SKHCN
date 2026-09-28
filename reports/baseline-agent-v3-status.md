# Trạng Thái Đo Agentic RAG v3

Ngày: 2026-09-16. **Đã hoàn tất đo pipeline đối chứng; chấm được 35/36 lượt.**

## Đã Hoàn Thành

- Nhập 30 tài liệu mẫu từ file gốc, tạo 94 vector; chưa phải toàn bộ 1.673 file lưu trữ.
- Chốt 18 câu hỏi: 6 tra cứu chính xác, 7 hỏi nội dung, 2 so sánh, 3 không có đáp án.
- Bật `AGENT_ROUTING_MODE=selective`: đường nhanh cho metadata chính xác hoặc câu trả lời được kiểm chứng đầy đủ; các câu còn lại dùng agent.
- Tiếp tục đo theo yêu cầu người dùng sau khi bổ sung budget. Giữ 24 lượt thành công trước đó, chạy lại 11 lượt lỗi và bổ sung 1 lượt còn thiếu.
- Hoàn tất 36/36 kết quả cuối không lỗi pipeline. Chấm bù 13 lượt, lấy được thêm 12 điểm; còn thiếu `exact_006 / always` do đầu ra bộ chấm vượt giới hạn token.
- Cập nhật README và tài liệu kiến trúc. Không khởi chạy server web để đo.

## Kết Quả

Xem [báo cáo cuối](baseline-agent-v3-selective.md). So với luôn chạy agent, chọn lọc giảm P50 từ 8,04 xuống 5,99 giây và lượt gọi AI trung bình từ 3,33 xuống 2,00. P95 gần như không đổi: 43,61 / 43,09 giây.

Chưa kết luận độ chính xác tăng: bộ chấm AI cho điểm không nhất quán với câu trả lời từ chối giống nhau. Mẫu nhỏ, câu hỏi sinh tự động; chưa đo tải đồng thời, Cloudflare hoặc tổng hợp số liệu. Cần bộ đáp án được cán bộ nghiệp vụ xác nhận.

## Dữ Liệu Được Giữ

- `reports/generated/v3-20260916/questions.jsonl`: bộ câu hỏi cố định.
- `reports/generated/v3-20260916/paired-run-02/manifest.json`: snapshot dữ liệu, mã nguồn và model.
- `results.jsonl`, `resumes.jsonl`, `judge_retries.jsonl` trong thư mục lượt đo: toàn bộ lần thử, lịch sử tiếp tục và chấm bù, kể cả lỗi.
- `final_summary.json` trong thư mục lượt đo: số liệu cuối sau chấm bù. `summary.json` là số liệu trước chấm bù.
- `reports/baseline-agent-v3-selective.md`: báo cáo tổng hợp và nhận xét sau đối chiếu.
- Baseline v1/v2 và lượt dở `paired-run-01` được giữ nguyên; không so trực tiếp v1/v2 với corpus mới.

Dữ liệu thô trong `reports/generated/` chỉ lưu cục bộ, không tự đưa lên GitHub. Không cần chạy lại lượt này; phép đo tương lai phải dùng đường dẫn mới để bảo toàn kết quả đối chiếu. Công cụ có `--resume` cho lượt bị gián đoạn, kiểm tra snapshot/model/mã ứng dụng trước khi tiếp tục và giữ mọi lần thử cũ.
