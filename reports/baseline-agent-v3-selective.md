# Agentic RAG v3: Đo Thử Chế Độ Chọn Lọc

- Ngày chạy: 2026-09-16T07:36:55+07:00
- Mẫu: 30 tài liệu, 94 vector; 18 câu hỏi, mỗi câu chạy ở cả hai chế độ.
- Cùng snapshot, cùng câu hỏi, chạy xen kẽ thứ tự; mô hình được làm nóng trước khi đo.
- Model trả lời: `qwen/qwen-2.5-72b-instruct`; model chấm: `google/gemini-2.5-pro`.
- Snapshot SHA-256: `f709d5f096ff6cebc9bb6111b13f8277b5290dcf704b060f397decc6f80de64d`.
- Dữ liệu chi tiết: `reports/generated/v3-20260916/paired-run-02`. Bộ câu hỏi: `reports/generated/v3-20260916/questions.jsonl`.

## Kết Quả

| Chỉ số | Luôn chạy agent | Chọn lọc |
|---|---:|---:|
| Số câu | 18 | 18 |
| Lỗi pipeline | 0 | 0 |
| P50 (giây) | 8.039 | 5.988 |
| P95 (giây) | 43.611 | 43.091 |
| Lượt gọi AI trung bình | 3.333 | 2.000 |
| Token pipeline trung bình (provider báo cáo) | 8305.167 | 5959.667 |
| Số câu có điểm chấm | 17 | 18 |
| Độ đúng trung bình (0–2) | 1.176 | 1.333 |
| Độ đầy đủ trung bình (0–2) | 1.353 | 1.444 |
| Tỷ lệ nhận định được hỗ trợ (LLM chấm) | 0.666 | 0.722 |
| Tỷ lệ câu có nguồn hỗ trợ (LLM chấm) | 0.941 | 1.000 |
| Recall văn bản kỳ vọng | 1.000 | 1.000 |
| Tỷ lệ trích đủ văn bản kỳ vọng | 0.933 | 1.000 |

## Theo Nhóm Câu Hỏi

| Nhóm | Số câu | P50 luôn / chọn lọc (giây) | Điểm đúng luôn / chọn lọc (0–2) |
|---|---:|---:|---:|
| compare | 2 | 30.159 / 36.978 | 1.000 / 2.000 |
| exact_lookup | 6 | 12.433 / 0.005 | 1.400 / 1.667 |
| no_answer | 3 | 1.038 / 0.946 | 0.667 / 0.000 |
| semantic_qa | 7 | 8.039 / 8.656 | 1.286 / 1.429 |

## Nhận Xét Sau Đối Chiếu

- Phép đo pipeline hoàn tất ngày 2026-09-16 sau khi người dùng bổ sung budget và yêu cầu tiếp tục. Có 36/36 kết quả cuối không lỗi; các lần thử lỗi trước đó vẫn được giữ, không bị xóa khỏi dữ liệu thô.
- Chọn lọc sử dụng 5 lượt metadata, 3 lượt trả lời nhanh có kiểm chứng và 10 lượt agent. P50 giảm khoảng 25,5%, lượt gọi AI trung bình giảm 40%, token pipeline được báo cáo giảm khoảng 28,2%. Đây không phải mức giảm chi phí bằng USD.
- P95 chỉ giảm khoảng 1,2%; nhóm semantic và compare không nhanh hơn trong lượt đo này. Không suy rộng rằng mọi câu hỏi đều nhanh hơn.
- Có 35/36 lượt có điểm chấm. Lượt `exact_006 / always` vẫn thiếu điểm do đầu ra của bộ chấm vượt giới hạn token; không gán điểm thay thế.
- Bộ chấm chưa ổn định: tại `no_answer_002`, hai chế độ trả lời giống hệt nhau nhưng nhận điểm 2 và 0. Cả 6 kết quả của nhóm không có đáp án đều thông báo không tìm thấy thông tin và có 0 nguồn. Giữ nguyên điểm thô để kiểm toán, nhưng không dùng điểm nhóm này để kết luận khác biệt chất lượng hoặc tỷ lệ bịa đáp án.
- Recall văn bản kỳ vọng đạt 1,0 chỉ cho thấy truy xuất có văn bản cần tìm, không chứng minh câu trả lời đầy đủ hay số liệu chính xác. Các điểm thấp ở `exact_005`, `semantic_004` và `compare_001` cần đối chiếu thủ công với nguồn trước khi quyết định sửa retrieval, metadata hay cách tổng hợp câu trả lời.
- Ưu tiên tiếp theo: chuẩn hóa rubric chấm (từ chối đúng phải được tính đúng; không dùng kiến thức ngoài nguồn), xác nhận câu hỏi/đáp án bởi người dùng nghiệp vụ, rồi mở rộng bộ đo sang PDF scan, phụ lục/bảng dài và tổng hợp số liệu có đáp án chuẩn.

## Phạm Vi Đo

- Đây là pilot trên tài liệu ngắn được lấy mẫu từ file gốc, không phải toàn bộ kho; không đại diện cho PDF scan, bảng lớn hoặc tải đồng thời.
- Câu hỏi được sinh tự động; điểm chất lượng do LLM chấm, chưa được chuyên gia xác nhận. Điểm trung bình chỉ tính các lượt có điểm chấm; số lượt đã chấm được ghi rõ ở bảng.
- Thời gian là pipeline trong tiến trình, không bao gồm HTTP, Cloudflare, thời gian làm nóng mô hình hoặc thời gian chấm. Token/lượt gọi của bộ chấm được loại khỏi số liệu pipeline.
- Đo ghép cặp, có thể tiếp tục qua nhiều phiên; mọi lần thử gốc được giữ trong results.jsonl và thông tin tiếp tục trong resumes.jsonl nếu có. Không phải kiểm định thống kê.
- Chưa đo chính xác tổng hợp số liệu hoặc hiệu lực pháp lý. Các chức năng này vẫn cần bộ gold riêng.
- Không so trực tiếp với baseline v1/v2 vì database đã reset và tập dữ liệu khác. Các baseline cũ được giữ nguyên.
- Có 13 lượt chấm lại do thiếu điểm; không chạy lại câu trả lời. Lượt đo paired-run-01 trước đó bị dừng vì lỗi provider/hạn mức và không dùng cho bảng này.

## Phụ Lục: Bộ Câu Hỏi Kiểm Thử

Toàn bộ 18 câu hỏi dưới đây giữ nguyên nội dung và thứ tự của bộ đo đã chốt. Đáp án kỳ vọng và trích đoạn là dữ liệu kiểm thử sinh tự động, chưa được chuyên gia xác nhận; không phải kết luận pháp lý.

Bảng từng câu dùng kết quả pipeline cuối cùng và điểm chấm bù đã lưu. Điểm đúng là điểm AI thô (0–2), không phải đánh giá của cán bộ; dấu '-' nghĩa là chưa có điểm. Các điểm không nhất quán vẫn được giữ nguyên, không sửa để làm đẹp kết quả.

### 1. no_answer_002

Nhóm: Không có đáp án.

> Văn bản 976956/ZZ-KHONGTONTAI quy định những nhiệm vụ nào?

Văn bản đối chiếu: Không có văn bản kỳ vọng trong kho.

Đáp án kỳ vọng:
- Thông báo không tìm thấy hoặc không đủ bằng chứng; không suy diễn nhiệm vụ của văn bản không tồn tại.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 1.818 | 1 | 2 | Không |
| Chọn lọc | agent | 0.946 | 1 | 0 | Không |

### 2. exact_004

Nhóm: Tra cứu chính xác.

> Văn bản 7322/GM-HĐKH có nội dung chính là gì?

Văn bản đối chiếu: 7322/GM-HĐKH.

Đáp án kỳ vọng:
- Giấy mời họp Hội đồng Khoa học và Công nghệ tỉnh nghiệm thu đề tài nghiên cứu khoa học cấp tỉnh: “Nghiên cứu xây dựng mô hình phát triển kinh tế dưới tán rừng trồng bằng cây Khôi tía tại tỉnh Lào Cai”.

Trích đoạn đối chiếu (7322/GM-HĐKH; doc_id=30):

> Giấy mời họp Hội đồng Khoa học và Công nghệ tỉnh nghiệm thu đề tài nghiên cứu khoa học cấp tỉnh: “Nghiên cứu xây dựng mô hình phát triển kinh tế dưới tán rừng trồng bằng cây Khôi tía tại tỉnh Lào Cai”.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 6.611 | 3 | 2 | Không |
| Chọn lọc | metadata | 0.010 | 0 | 2 | Không |

### 3. semantic_001

Nhóm: Hỏi nội dung.

> Việc đầu tư xây dựng trạm thu phát sóng di động có phù hợp với chủ trương của tỉnh Lào Cai không?

Văn bản đối chiếu: 2160/SKHCN-BCVT.

Đáp án kỳ vọng:
- Việc đầu tư xây dựng trạm thu phát sóng di động để xóa vùng lõm sóng trên địa bàn tỉnh Lào Cai là hoàn toàn phù hợp với chủ trương, định hướng phát triển của tỉnh.

Trích đoạn đối chiếu (2160/SKHCN-BCVT; doc_id=2):

> Việc đầu tư xây dựng trạm thu phát sóng di động để xóa vùng lõm sóng trên địa bàn tỉnh Lào Cai là hoàn toàn phù hợp với chủ trương, định hướng phát triển của tỉnh, đặc biệt là Kế hoạch hành động số 01-KH/TU ngày 01/7/2025 của Ban Thường vụ Tỉnh ủy thực hiện Nghị quyết số 57-NQ/TW của Bộ Chính trị và Kế hoạch số 34-KH/TU ngày 16/01/2026 của Ban Thường vụ Tỉnh ủy về phát triển khoa học, công nghệ, đổi mới sáng tạo và chuyển đổi số năm 2026.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 12.852 | 3 | 0 | Không |
| Chọn lọc | verified_fast | 12.088 | 2 | 2 | Không |

### 4. semantic_003

Nhóm: Hỏi nội dung.

> Đề tài nghiên cứu khoa học cấp tỉnh về mô hình phát triển kinh tế dưới tán rừng trồng bằng cây Khôi tía sẽ được nghiệm thu vào thời gian nào?

Văn bản đối chiếu: 7322/GM-HĐKH.

Đáp án kỳ vọng:
- Đề tài sẽ được nghiệm thu vào lúc 14h00’ ngày 23 tháng 7 năm 2026.

Trích đoạn đối chiếu (7322/GM-HĐKH; doc_id=30):

> 14h00’ ngày 23 tháng 7 năm 2026 (thứ Năm).

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 6.170 | 3 | 2 | Không |
| Chọn lọc | verified_fast | 5.988 | 2 | 0 | Không |

### 5. exact_006

Nhóm: Tra cứu chính xác.

> Văn bản 2200/SKHCN-BCVT có nội dung chính là gì?

Văn bản đối chiếu: 2200/SKHCN-BCVT.

Đáp án kỳ vọng:
- Nội dung tham luận tại Hội nghị Tổng kết thực hiện Luật Quốc phòng, Luật Dân quân tự vệ và Luật Giáo dục QPAN

Trích đoạn đối chiếu (2200/SKHCN-BCVT; doc_id=28):

> Nội dung tham luận tại Hội nghị Tổng kết thực hiện Luật Quốc phòng, Luật Dân quân tự vệ và Luật Giáo dục QPAN

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 7.541 | 3 | - | Không |
| Chọn lọc | metadata | 0.005 | 0 | 2 | Không |

### 6. compare_002

Nhóm: So sánh.

> So sánh nội dung chính của văn bản 65/GP-SKHCN với văn bản 7322/GM-HĐKH.

Văn bản đối chiếu: Không có văn bản kỳ vọng trong kho.

Đáp án kỳ vọng:
- Cấp phép tiến hành công việc bức xạ (Sử dụng thiết bị X-quang chẩn đoán y tế) cho Phòng khám đa khoa Kinh Bắc.
- Giấy mời họp Hội đồng Khoa học và Công nghệ tỉnh nghiệm thu đề tài nghiên cứu khoa học cấp tỉnh: “Nghiên cứu xây dựng mô hình phát triển kinh tế dưới tán rừng trồng bằng cây Khôi tía tại tỉnh Lào Cai”.

Trích đoạn đối chiếu (65/GP-SKHCN; doc_id=4):

> Cấp phép tiến hành công việc bức xạ (Sử dụng thiết bị X-quang chẩn đoán y tế) cho Phòng khám đa khoa Kinh Bắc.

Trích đoạn đối chiếu (7322/GM-HĐKH; doc_id=30):

> Giấy mời họp Hội đồng Khoa học và Công nghệ tỉnh nghiệm thu đề tài nghiên cứu khoa học cấp tỉnh: “Nghiên cứu xây dựng mô hình phát triển kinh tế dưới tán rừng trồng bằng cây Khôi tía tại tỉnh Lào Cai”.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 43.611 | 6 | 2 | Không |
| Chọn lọc | agent | 43.091 | 6 | 2 | Không |

### 7. exact_002

Nhóm: Tra cứu chính xác.

> Văn bản 386/BC-SKHCN có nội dung chính là gì?

Văn bản đối chiếu: 386/BC-SKHCN.

Đáp án kỳ vọng:
- Báo cáo việc thực hiện chế độ theo dõi, cảnh báo, báo cáo trực tuyến tiến độ nhiệm vụ thực hiện Nghị quyết số 57-NQ/TW của Bộ Chính trị.

Trích đoạn đối chiếu (386/BC-SKHCN; doc_id=18):

> Báo cáo việc thực hiện chế độ theo dõi, cảnh báo, báo cáo trực tuyến tiến độ nhiệm vụ thực hiện Nghị quyết số 57-NQ/TW của Bộ Chính trị.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 38.620 | 5 | 2 | Không |
| Chọn lọc | metadata | 0.006 | 0 | 2 | Không |

### 8. semantic_005

Nhóm: Hỏi nội dung.

> Sở Khoa học và Công nghệ cần chuẩn bị nội dung tham luận gì để gửi về Ủy ban nhân dân tỉnh?

Văn bản đối chiếu: 2200/SKHCN-BCVT.

Đáp án kỳ vọng:
- Sở Khoa học và Công nghệ cần chuẩn bị nội dung tham luận về “Công tác bảo đảm thông tin liên lạc cho lãnh đạo, chỉ huy trong hoạt động khu vực phòng thủ; những hạn chế, bất cập, khó khăn, vướng mắc và kiến nghị đề xuất”.

Trích đoạn đối chiếu (2200/SKHCN-BCVT; doc_id=28):

> Trong đó Ủy ban nhân dân tỉnh giao Sở Khoa học và Công nghệ chuẩn bị nội dung tham luận “Công tác bảo đảm thông tin liên lạc cho lãnh đạo, chỉ huy trong hoạt động khu vực phòng thủ; những hạn chế, bất cập, khó khăn, vướng mắc và kiến nghị đề xuất” gửi về Ủy ban nhân dân tỉnh (qua Bộ Chỉ huy quân sự) trước ngày 20/7/2026.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 8.039 | 3 | 2 | Không |
| Chọn lọc | agent | 8.356 | 3 | 2 | Không |

### 9. no_answer_003

Nhóm: Không có đáp án.

> Văn bản 984875/ZZ-KHONGTONTAI quy định những nhiệm vụ nào?

Văn bản đối chiếu: Không có văn bản kỳ vọng trong kho.

Đáp án kỳ vọng:
- Thông báo không tìm thấy hoặc không đủ bằng chứng; không suy diễn nhiệm vụ của văn bản không tồn tại.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 1.038 | 1 | 0 | Không |
| Chọn lọc | agent | 0.954 | 1 | 0 | Không |

### 10. semantic_004

Nhóm: Hỏi nội dung.

> Sở Khoa học và Công nghệ tỉnh Lào Cai sẽ nghỉ lễ Quốc khánh trong khoảng thời gian nào?

Văn bản đối chiếu: /TB-SKHCN.

Đáp án kỳ vọng:
- Sở Khoa học và Công nghệ tỉnh Lào Cai sẽ nghỉ từ Thứ 7 ngày 29/8/2026 đến hết Thứ 4 ngày 02/9/2026.

Trích đoạn đối chiếu (/TB-SKHCN; doc_id=10):

> Nghỉ từ Thứ 7 ngày 29/8/2026 đến hết Thứ 4 ngày 02/9/2026.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 5.234 | 3 | 0 | Không |
| Chọn lọc | agent | 10.256 | 3 | 2 | Không |

### 11. no_answer_001

Nhóm: Không có đáp án.

> Văn bản 969037/ZZ-KHONGTONTAI quy định những nhiệm vụ nào?

Văn bản đối chiếu: Không có văn bản kỳ vọng trong kho.

Đáp án kỳ vọng:
- Thông báo không tìm thấy hoặc không đủ bằng chứng; không suy diễn nhiệm vụ của văn bản không tồn tại.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 0.902 | 1 | 0 | Không |
| Chọn lọc | agent | 0.315 | 1 | 0 | Không |

### 12. exact_003

Nhóm: Tra cứu chính xác.

> Văn bản 65/GP-SKHCN có nội dung chính là gì?

Văn bản đối chiếu: 65/GP-SKHCN.

Đáp án kỳ vọng:
- Cấp phép tiến hành công việc bức xạ (Sử dụng thiết bị X-quang chẩn đoán y tế) cho Phòng khám đa khoa Kinh Bắc.

Trích đoạn đối chiếu (65/GP-SKHCN; doc_id=4):

> Cấp phép tiến hành công việc bức xạ (Sử dụng thiết bị X-quang chẩn đoán y tế) cho Phòng khám đa khoa Kinh Bắc.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 19.322 | 3 | 1 | Không |
| Chọn lọc | metadata | 0.004 | 0 | 2 | Không |

### 13. exact_001

Nhóm: Tra cứu chính xác.

> Văn bản 2160/SKHCN-BCVT có nội dung chính là gì?

Văn bản đối chiếu: 2160/SKHCN-BCVT.

Đáp án kỳ vọng:
- Phối hợp cho ý kiến, giải quyết đề xuất của Viettel Lào Cai về việc xây dựng trạm thu phát sóng di động phục vụ xóa lõm sóng trên địa bàn tỉnh.

Trích đoạn đối chiếu (2160/SKHCN-BCVT; doc_id=2):

> Phối hợp cho ý kiến, giải quyết đề xuất của Viettel Lào Cai về việc xây dựng trạm thu phát sóng di động phục vụ xóa lõm sóng trên địa bàn tỉnh.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 35.260 | 5 | 2 | Không |
| Chọn lọc | metadata | 0.002 | 0 | 2 | Không |

### 14. semantic_002

Nhóm: Hỏi nội dung.

> Sở Khoa học và Công nghệ đang nghiên cứu xây dựng biểu mẫu, chế độ báo cáo trên hệ thống nào?

Văn bản đối chiếu: 386/BC-SKHCN.

Đáp án kỳ vọng:
- Hệ thống báo cáo của tỉnh (baocao.laocai.gov.vn).

Trích đoạn đối chiếu (386/BC-SKHCN; doc_id=18):

> Sở Khoa học và Công nghệ đang nghiên cứu để xây dựng biểu mẫu, chế độ báo cáo trên Hệ thống báo cáo của tỉnh (baocao.laocai.gov.vn).

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 6.079 | 3 | 2 | Không |
| Chọn lọc | agent | 11.823 | 3 | 2 | Không |

### 15. semantic_006

Nhóm: Hỏi nội dung.

> Sở Khoa học và Công nghệ đã tham mưu UBND tỉnh ban hành những văn bản nào để đấu tranh, ngăn chặn, xử lý hành vi xâm phạm quyền sở hữu trí tuệ?

Văn bản đối chiếu: 431/BC-SKHCN.

Đáp án kỳ vọng:
- Sở Khoa học và Công nghệ đã tham mưu UBND tỉnh ban hành Quyết định số 1686/QĐ-UBND, Văn bản số 5765/UBND-NC, số 6544/UBND-NC và Kế hoạch số 309/KH-UBND.

Trích đoạn đối chiếu (431/BC-SKHCN; doc_id=20):

> Sở Khoa học và Công nghệ đã tham mưu UBND tỉnh ban hành Quyết định số 1686/QĐ-UBND, Văn bản số 5765/UBND-NC, số 6544/UBND-NC và Kế hoạch số 309/KH-UBND.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 8.911 | 3 | 2 | Không |
| Chọn lọc | agent | 8.656 | 3 | 2 | Không |

### 16. exact_005

Nhóm: Tra cứu chính xác.

> Văn bản /TB-SKHCN có nội dung chính là gì?

Văn bản đối chiếu: /TB-SKHCN.

Đáp án kỳ vọng:
- Hoán đổi ngày làm việc, thời gian nghỉ và lịch trực cơ quan dịp nghỉ Lễ Quốc khánh 02/9/2026

Trích đoạn đối chiếu (/TB-SKHCN; doc_id=10):

> Hoán đổi ngày làm việc, thời gian nghỉ và lịch trực cơ quan dịp nghỉ Lễ Quốc khánh 02/9/2026

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 12.433 | 5 | 0 | Không |
| Chọn lọc | agent | 12.683 | 5 | 0 | Không |

### 17. semantic_007

Nhóm: Hỏi nội dung.

> Công ty Cổ phần KK SA PA được phép sử dụng loại thiết bị vô tuyến điện nào?

Văn bản đối chiếu: 7406/GP-SKHCN.

Đáp án kỳ vọng:
- Thiết bị bộ đàm KBC PT7000.

Trích đoạn đối chiếu (7406/GP-SKHCN; doc_id=17):

> Loại: thiết bị bộ đàm: KBC PT7000.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 10.437 | 5 | 1 | Không |
| Chọn lọc | verified_fast | 6.851 | 2 | 0 | Không |

### 18. compare_001

Nhóm: So sánh.

> So sánh nội dung chính của văn bản 2160/SKHCN-BCVT với văn bản 386/BC-SKHCN.

Văn bản đối chiếu: Không có văn bản kỳ vọng trong kho.

Đáp án kỳ vọng:
- Phối hợp cho ý kiến, giải quyết đề xuất của Viettel Lào Cai về việc xây dựng trạm thu phát sóng di động phục vụ xóa lõm sóng trên địa bàn tỉnh.
- Báo cáo việc thực hiện chế độ theo dõi, cảnh báo, báo cáo trực tuyến tiến độ nhiệm vụ thực hiện Nghị quyết số 57-NQ/TW của Bộ Chính trị.

Trích đoạn đối chiếu (2160/SKHCN-BCVT; doc_id=2):

> Phối hợp cho ý kiến, giải quyết đề xuất của Viettel Lào Cai về việc xây dựng trạm thu phát sóng di động phục vụ xóa lõm sóng trên địa bàn tỉnh.

Trích đoạn đối chiếu (386/BC-SKHCN; doc_id=18):

> Báo cáo việc thực hiện chế độ theo dõi, cảnh báo, báo cáo trực tuyến tiến độ nhiệm vụ thực hiện Nghị quyết số 57-NQ/TW của Bộ Chính trị.

| Chế độ | Nhánh xử lý | Giây | Lượt gọi AI | Điểm đúng (0–2) | Lỗi pipeline |
|---|---|---:|---:|---:|---|
| Luôn chạy agent | agent | 30.159 | 4 | 0 | Không |
| Chọn lọc | agent | 36.978 | 4 | 2 | Không |
