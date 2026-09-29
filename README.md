# DocNexus (QLVB AI v4)

DocNexus là web app hỗ trợ cán bộ, công chức và viên chức tra cứu, tổng hợp và kiểm tra văn bản hành chính. Hệ thống lưu dữ liệu gốc trong PostgreSQL, lập chỉ mục vector trên Qdrant, sử dụng Agentic RAG để trả lời có nguồn và dùng Playwright để thu thập văn bản Đi/Đến từ hệ thống QLVB.

> Trạng thái hiện tại: Agentic RAG v4 (index `agentic-v4`, collection `docnexus_agentic_v4`). Dữ liệu được tổ chức theo **văn bản** gồm nhiều **tệp** (bản chính, bản sao cùng nội dung, tệp đính kèm). Câu trả lời được **stream thật** rồi kiểm chứng từng đoạn. Tổng hợp số liệu dùng Aggregation v2 (LLM chỉ trích dữ kiện, code tính toán). Nâng cấp từ v3: xem mục [Nâng cấp lên v4](#nâng-cấp-lên-v4).

## Chức năng hiện có

### Tra cứu và hỏi đáp văn bản

- Tìm kiếm hybrid bằng vector dense, sparse và Reciprocal Rank Fusion (RRF); rerank bằng BAAI/bge-reranker-v2-m3 kèm ngữ cảnh văn bản (số ký hiệu, trích yếu, mục).
- Tìm chính xác theo số ký hiệu (kể cả dạng `57-NQ/TW`) và theo số rút gọn ("công văn 2072") khi số đó xác định duy nhất một văn bản.
- Lọc nhiều giá trị theo loại văn bản, hướng Đi/Đến, lĩnh vực, thời gian (năm, quý, tháng, 6 tháng, khoảng ngày) và cơ quan ban hành; bộ lọc được đẩy xuống Qdrant/PostgreSQL trước khi truy xuất.
- Trả lời stream theo thời gian thực (Server-Sent Events), kèm nguồn, trạng thái xử lý và mức độ tin cậy; nguồn ghi rõ tệp đính kèm.

### Agentic RAG v4

- Lập kế hoạch MỘT lần cho mỗi câu hỏi: luật trước; LLM chỉ được gọi để tách khía cạnh cho câu hỏi phức tạp (so sánh, hiệu lực, nhiều ý).
- Truy xuất đa khía cạnh: mỗi khía cạnh được rerank với chính truy vấn của nó và có hạn mức kết quả, tránh để một ý lấn át.
- Cổng độ phủ bằng luật (điểm rerank, văn bản được nêu tên, từng khía cạnh). Chỉ khi chưa đủ mới gọi LLM chấm bằng chứng; cùng lượt đó LLM đề xuất truy vấn bổ sung (corrective RAG, tối đa `AGENT_MAX_ATTEMPTS` vòng).
- Mở rộng chunk lân cận (theo từng tệp) và mục cha có giới hạn độ dài.
- Quan hệ căn cứ/sửa đổi/thay thế/bãi bỏ/liên quan được trích tự động khi nạp và dùng cho câu hỏi hiệu lực (đánh dấu chưa xác minh).
- Kiểm chứng từng đoạn của câu trả lời; đoạn không có bằng chứng bị lược bỏ, định dạng Markdown được giữ nguyên. Verifier lỗi thì không phát hành bản nháp.
- Log kế hoạch, nguồn, điểm rerank, số vòng, thời gian từng bước (`timings`) và đường xử lý.

### Tổng hợp số liệu (Aggregation v2)

LLM là bộ trích xuất dữ kiện có trích dẫn, không phải máy tính:

1. Kế hoạch có kiểu: chỉ số, phép tính (sum, count, count_documents, avg, min, max, distinct_count), đơn vị, trạng thái (kế hoạch/thực hiện/lũy kế), phạm vi, group_by.
2. PostgreSQL liệt kê đầy đủ văn bản trong phạm vi (phân trang; giới hạn `AGG_MAX_DOCS`, báo rõ khi bị cắt).
3. Hybrid search theo nhóm văn bản lấy các đoạn liên quan trong toàn văn (không cắt 8.000 ký tự đầu); reranker loại văn bản không liên quan.
4. Mỗi văn bản trả nhiều dữ kiện (kể cả từng dòng bảng), gắn đoạn nguồn, trích dẫn nguyên văn, trạng thái và độ tin cậy; trích song song có giới hạn.
5. Code chuẩn hóa số kiểu Việt Nam (1.234,5; nghìn/triệu/tỷ; %) và kiểm tra trích dẫn có thật trong đoạn nguồn, con số có trong trích dẫn.
6. Chống cộng trùng: dòng tổng thay dòng chi tiết trong cùng văn bản; fact lặp giữa đoạn/văn bản.
7. Tính toán và nhóm bằng code; kiểm tra bất biến (tổng chi tiết so với dòng tổng, một đơn vị, tổng nhóm = tổng chung).
8. Dữ kiện nghi vấn (độ tin cậy thấp, khác trạng thái, khác đơn vị, dấu phân cách mơ hồ, trích dẫn không khớp) đưa vào danh sách cần cán bộ xác nhận, không cộng vào kết quả.

Kết quả vẫn là tổng hợp tự động: cần kiểm tra đơn vị, kỳ báo cáo và phạm vi trước khi dùng cho báo cáo chính thức.

### Thu thập và nhập dữ liệu

- Crawl Văn bản Đi, Văn bản Đến hoặc toàn bộ hệ thống QLVB; đăng nhập SSO qua captcha trên trang quản trị.
- Dùng nhiều locator dự phòng, thử lại khi điều hướng chậm và tự đăng nhập lại khi phiên hết hạn; checkpoint SQLite.
- **Gom tệp theo văn bản**: các tệp của một văn bản (bản ký số, bản DOCX, phụ lục, văn bản kèm theo) được nạp cùng nhau. Metadata được AI trích **một lần** từ tệp chính.
- **Phát hiện tệp trùng nội dung** (bản PDF ký số và DOCX của cùng văn bản): chỉ index một bản (ưu tiên bản trích xuất sạch nhất), các bản còn lại được ghi nhận là bản sao.
- Làm sạch giá trị giữ chỗ "undefined" từ giao diện QLVB; dựng lại số ký hiệu từ dòng "Số: …" khi văn bản ký số tách con số khỏi ký hiệu.
- Ghi index theo lượt: điểm mới ghi xong mới xóa lượt cũ và công bố, không để lộ trạng thái nửa vời.
- Giữ bản gốc trong data/store, chống nạp trùng bằng SHA-256; hỗ trợ PDF, DOC/DOCX, XLS/XLSX, CSV và OCR ảnh/PDF scan. Tệp lỗi được cách ly kèm lý do cụ thể.

### Tiện ích nghiệp vụ

- Kiểm tra thể thức (.docx) và nội dung dự thảo (.docx, .doc, .pdf).
- OCR tài liệu (PDF hoặc ảnh) qua máy chủ Unlimited-OCR/SGLang tùy chọn, gửi theo lô trang.
- Gỡ băng âm thanh bằng Gemini và tạo bản tóm tắt có cấu trúc.
- Trang quản trị hiển thị thống kê (chỉ văn bản sẵn sàng, phân bố trạng thái, vai trò tệp), danh sách văn bản kèm số tệp/đoạn và trạng thái crawler.
- Giao diện responsive, sáng/tối, PWA có offline fallback.

## Kiến trúc

~~~mermaid
flowchart LR
    QLVB[Hệ thống QLVB] --> Crawler[Playwright crawler]
    Upload[Tệp tải lên] --> Extract[Trích xuất và OCR]
    Crawler --> Extract
    Extract --> Metadata[Metadata và structured chunking]
    Metadata --> PG[(PostgreSQL)]
    Metadata --> Embed[BGE-M3 dense và sparse]
    Embed --> QD[(Qdrant)]

    Query[Câu hỏi] --> Planner[Query analyzer và planner]
    Planner --> Exact[Tra cứu chính xác PostgreSQL]
    Planner --> Hybrid[Hybrid retrieval Qdrant]
    Exact --> Rerank[Rerank và mở rộng ngữ cảnh]
    Hybrid --> Rerank
    Rerank --> Answer[LLM trả lời]
    Answer --> Verify[Verifier và trích nguồn]
    Verify --> UI[Web app và PWA]
~~~

### Thành phần chính

| Thành phần | Vai trò |
|---|---|
| FastAPI + Jinja2 | Web app, API và trang quản trị |
| PostgreSQL | Văn bản đầy đủ, metadata, quan hệ và log RAG |
| Qdrant | Chunk, dense vector và sparse vector |
| BGE-M3 | Embedding dense/sparse |
| BGE reranker | Xếp hạng lại bằng chứng |
| OpenRouter/Gemini | Metadata, lập kế hoạch, trả lời và kiểm chứng |
| Playwright | Crawl QLVB qua SSO |
| SQLite | Checkpoint crawler |

## Cài đặt

### Yêu cầu

- Linux hoặc WSL2.
- Docker và Docker Compose.
- Python 3.10 trở lên.
- Tesseract, Poppler và LibreOffice nếu cần OCR hoặc xử lý định dạng cũ.
- Khóa OpenRouter; Gemini API key nếu dùng chức năng âm thanh.

### Khởi tạo môi trường

~~~bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt   # thêm requirements-dev.txt để chạy test/lint
PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" ./venv/bin/playwright install chromium
cp .env.example .env
~~~

Sửa .env trước khi chạy. Tối thiểu cần cấu hình khóa LLM và thay toàn bộ mật khẩu mặc định.

~~~env
OPENROUTER_API_KEY=...
ADMIN_USER=admin
ADMIN_PASS=<ít nhất 10 ký tự, không dùng mật khẩu mặc định>
QDRANT_API_KEY=...
QLVB_URL=https://dia-chi-he-thong-qlvb

# Tùy chọn
GEMINI_API_KEY=...
OCR_SERVER_URL=http://127.0.0.1:10000
~~~

Không commit .env, thông tin đăng nhập QLVB hoặc dữ liệu nội bộ lên Git.

## Khởi chạy

~~~bash
./run.sh
~~~

run.sh thực hiện lần lượt:

1. Nâng giới hạn file mở cho tiến trình ứng dụng trong giới hạn hệ điều hành cho phép.
2. Khởi động PostgreSQL và Qdrant bằng Docker Compose.
3. Chờ hai dịch vụ sẵn sàng và áp dụng migration còn thiếu.
4. Chạy DocNexus tại http://127.0.0.1:8081.

Đổi cổng khi cần:

~~~bash
APP_PORT=8090 ./run.sh
~~~

Cloudflare Tunnel nên trỏ origin tới:

~~~text
http://127.0.0.1:8081
~~~

Dừng web app bằng Ctrl+C. PostgreSQL và Qdrant vẫn tiếp tục chạy. Muốn dừng chúng mà không xóa dữ liệu:

~~~bash
docker compose stop
~~~

PostgreSQL và Qdrant được bind mount tại data/postgres và data/qdrant. Bản gốc cùng checkpoint crawler nằm theo STORE_DIR và CRAWLER_STATE_DB trong .env. Dừng hoặc tạo lại container không xóa các thư mục bind mount này.

## PWA

- Android/Chrome: dùng nút cài đặt trong giao diện khi trình duyệt phát sự kiện cài PWA.
- iPhone/iPad Safari: mở menu Chia sẻ, chọn **Thêm vào Màn hình chính**, sau đó xác nhận.
- Khi chạy ở chế độ standalone từ màn hình chính, nút cài PWA được ẩn tự động.
- Service worker chỉ hoạt động trên HTTPS hoặc localhost. Khi truy cập từ xa nên dùng domain HTTPS qua Cloudflare Tunnel.

## Crawler

Mở /admin, tab Crawler. Có hai cách thu thập:

### Đồng bộ qua API QLVB (khuyến nghị)

Dùng chính API mà giao diện QLVB gọi (danh sách Văn bản đi, danh sách văn thư Văn bản đến đã xử lý, chi tiết văn bản, tải tệp). Trình duyệt tự động chỉ dùng cho bước đăng nhập SSO. Crawler **không** gọi các API đánh dấu "đã xem", nên không làm thay đổi trạng thái đọc văn bản của người dùng.

Hai pha, tiến độ lưu trong PostgreSQL (bảng `crawl_items`, khoá là mã văn bản nội bộ của QLVB theo hướng):

1. **Kiểm kê**: đọc danh sách, biết tổng số trên QLVB, đã nạp, chờ tải, lỗi, bỏ qua.
   - *Kiểm tra văn bản mới*: chỉ đọc trang đầu và so sánh tổng số; khi có thay đổi mới quét lại hướng đó.
   - *Kiểm kê đầy đủ*: lật hết danh sách (chỉ JSON, vài phút cho khoảng 17.000 văn bản).
2. **Tải phần còn thiếu**: đọc chi tiết, tải tệp, nạp vào kho; văn bản mới nhất trước. Nhập số lượng hoặc 0 để tải tất cả.

Mỗi văn bản được ghi trạng thái ngay sau khi xử lý. Mất mạng, tắt máy hoặc bấm **Dừng** chỉ ảnh hưởng văn bản đang dở; lần chạy sau tự tiếp tục. Phiên QLVB hết hạn thì hộp đăng nhập hiện lại và tác vụ chạy tiếp sau khi nhập captcha. Văn bản lỗi được thử lại tối đa `CRAWLER_API_MAX_ATTEMPTS` lần; bảng "Danh sách theo dõi" cho phép lọc theo trạng thái, tải lại từng văn bản và xuất CSV. Văn bản mật và tệp không hỗ trợ (.rar, .zip) được ghi nhận là bỏ qua kèm lý do. Nếu danh sách của một văn bản thay đổi (thêm tệp), văn bản được đưa lại hàng chờ.

Hộp đăng nhập có nút **Đổi mã** và **Huỷ đăng nhập**. SSO không công bố thời hạn captcha, nên mã tự đổi sau `CRAWLER_CAPTCHA_TTL_SECONDS` (mặc định 180 giây, có đồng hồ đếm ngược); trang đăng nhập được mở lại sau `CRAWLER_LOGIN_PAGE_MAX_AGE_SECONDS`.

### Crawler giao diện (dự phòng)

Thao tác trên trang QLVB bằng Playwright như trước; chỉ dùng khi API không hoạt động. Cấu hình `CRAWLER_*` và checkpoint SQLite `CRAWLER_STATE_DB` vẫn giữ nguyên. Tệp lỗi được chuyển vào `STORE_DIR/failed_ingest`.

Khảo sát API được ghi bằng `scripts/record_qlvb_network.py` (mở trình duyệt để người dùng thao tác; token, cookie, mật khẩu được che trước khi lưu vào `data/captures/`).

## Agentic RAG

### Endpoint

| Endpoint | Ý nghĩa |
|---|---|
| GET /api/search_stream | Server-Sent Events: `status`, `sources`, `token` (bản nháp), `answer` (bản đã kiểm chứng), `done` (kết quả đầy đủ) |
| GET /api/search_agent_stream | Alias của /api/search_stream |
| GET /api/search | Kết quả hoàn chỉnh dạng JSON (tích hợp, đo đánh giá) |
| GET /api/aggregate_stream | Tổng hợp số liệu: tiến độ `status` rồi `aggregation_result` |
| GET /api/aggregate | Tổng hợp số liệu dạng JSON |

Tham số lọc nhận nhiều giá trị cách nhau bằng dấu phẩy: `loai_vb=bao_cao,ke_hoach`, `huong=den`, `linh_vuc=cds`.

### Cấu hình chính

| Cấu hình | Ý nghĩa |
|---|---|
| AGENT_ROUTING_MODE=selective | Luật quyết định khi nào cần LLM lập kế hoạch/chấm bằng chứng |
| AGENT_ROUTING_MODE=always | Luôn lập kế hoạch và chấm bằng LLM, phục vụ đo đối chứng |
| RAG_COLLECTION=docnexus_agentic_v4 | Collection của index agentic-v4 |
| AGENT_MAX_ATTEMPTS=2 | Số vòng truy xuất tối đa |
| RAG_RERANK_POOL=24 | Số ứng viên rerank cho mỗi câu hỏi (chia đều cho các khía cạnh) |
| LLM_FAST_TIMEOUT_SECONDS=20 | Timeout lời gọi phụ trợ (planner, grader, verifier) |
| WARMUP_MODELS=true | Nạp mô hình ngay khi khởi động |

Chunk 512 token, overlap 64 token; giữ trang PDF, ngữ cảnh bảng và đường dẫn Chương/Mục/Điều/Khoản. Mỗi văn bản có thêm một điểm tóm tắt (số ký hiệu, loại, cơ quan, trích yếu, chủ trương, tệp đính kèm). Embedding của từng đoạn kèm ngữ cảnh văn bản. Tối đa `RAG_CONCURRENCY` yêu cầu nặng đồng thời; quá tải trả HTTP 503 trước khi stream bắt đầu. Hạn 60 giây áp dụng cho các bước có thể ngắt và lời gọi mạng.

### Độ trễ

Những thay đổi chính để giảm độ trễ cảm nhận và P95:

- Stream token trả lời: người dùng thấy nội dung khi model bắt đầu sinh, không chờ hết pipeline.
- Không còn chạy hai lần lập kế hoạch/soạn trả lời khi đường nhanh thất bại. Bằng chứng đủ theo luật thì bỏ qua LLM chấm; chấm và viết lại truy vấn gộp làm một lượt.
- Verifier chỉ trả trạng thái từng đoạn (JSON rất ngắn) thay vì viết lại toàn bộ câu trả lời.
- Bản sao cùng nội dung không còn chiếm chỗ bằng chứng hay thời gian rerank. Cache embedding truy vấn; truy vấn Qdrant song song; làm nóng mô hình khi khởi động.
- Timings từng bước được lưu trong `rag_query_logs.plan.timings` để phân tích P95 thực tế.

Chưa có số đo P95 mới cho v4; cần chạy lại `scripts/benchmark_selective.py` sau khi reindex.

## Nâng cấp lên v4

Migration `004_document_files.sql` tách văn bản và tệp, gom các bản ghi cũ (mỗi tệp một dòng) theo khóa crawler. Sau đó phải tạo index mới:

~~~bash
# 1. Sao lưu
docker exec qlvb_postgres pg_dump -U qlvb qlvb > backup-before-v4.sql
# 2. Áp dụng migration (run.sh cũng tự làm bước này)
./venv/bin/python scripts/apply_migrations.py
# 3. Đổi RAG_COLLECTION trong .env thành docnexus_agentic_v4, rồi tạo index
./venv/bin/python scripts/reindex.py
~~~

`scripts/reindex.py` mặc định tái sử dụng văn bản đã trích xuất và metadata đã có (không gọi LLM, không OCR lại); thêm `--re-extract` hoặc `--refresh-metadata` khi cần. Có thể chạy tiếp bằng `--start-id`. Trong lúc reindex, văn bản chưa xong không được tìm thấy. Collection v3 cũ được giữ nguyên để đối chiếu; chỉ xóa khi không cần nữa.

`scripts/reset_agentic_data.py --confirm-delete-qlvb` chỉ dành cho đặt lại chủ động; xóa dữ liệu PostgreSQL, các collection của project và checkpoint crawler, giữ file gốc.

Hướng dẫn vận hành chi tiết nằm tại [docs/agentic-rag.md](docs/agentic-rag.md).

## Đánh giá chất lượng

**Số đo dưới đây là của v3 (trước khi gom tệp và stream).** V3 đã hoàn tất đo đối chứng trên mẫu 30 tài liệu, 18 câu hỏi (36 lượt). Xem [báo cáo chi tiết](reports/baseline-agent-v3-selective.md) và [trạng thái dữ liệu đã lưu](reports/baseline-agent-v3-status.md). So với luôn chạy agent, chế độ chọn lọc có P50 5,99 giây thay vì 8,04 giây, trung bình 2,00 thay vì 3,33 lượt gọi AI; P95 gần như không đổi (43,09 / 43,61 giây).

Không có lỗi pipeline trong 36 kết quả cuối; 35/36 lượt có điểm chấm AI. Bộ chấm có trường hợp chấm khác nhau cho câu trả lời từ chối giống hệt nhau, nên chưa thể khẳng định độ chính xác tăng. Đây chưa phải phép đo toàn kho, tải đồng thời, Cloudflare hoặc tổng hợp số liệu.

Baseline lịch sử được giữ nguyên tại [reports/baseline-v1.md](reports/baseline-v1.md) và [reports/baseline-agent-v2.md](reports/baseline-agent-v2.md). Database đã reset nên không thể so trực tiếp điểm v3 với các báo cáo này như cùng một tập dữ liệu.

Kết quả lịch sử trên bộ pilot 10 câu hỏi (không phải v3):

| Chỉ số | RAG v1 | Agentic v2 |
|---|---:|---:|
| Hit@1 | 25,0% | 75,0% |
| Hit@5 | 50,0% | 87,5% |
| Hit@10 | 62,5% | 100,0% |
| MRR | 0,37 | 0,807 |
| Grounded claim ratio | 14,3% | 70,0% |
| P95 latency | 13,8 giây | 66,1 giây |

Bộ pilot cũ nhỏ; các số đo trên không xác nhận chất lượng bản v3 hiện tại.

Đo lại sau khi có dữ liệu ready (chọn đường dẫn mới cho mỗi lần chạy):

~~~bash
./venv/bin/python scripts/generate_eval_set.py --count 16 --comparison-count 2 --output reports/generated/v3-next/questions.jsonl
./venv/bin/python scripts/benchmark_selective.py --input reports/generated/v3-next/questions.jsonl --output reports/generated/v3-next/paired-run
./venv/bin/python scripts/report_selective.py --run reports/generated/v3-next/paired-run --input reports/generated/v3-next/questions.jsonl --output reports/baseline-agent-v3-next.md --retry-missing
~~~

Phép đo này gọi trực tiếp pipeline, không mở server web. Hai chế độ chạy xen kẽ trên cùng snapshot, có làm nóng mô hình trước. Kết quả lưu câu trả lời/nguồn, quyết định định tuyến, thời gian, lượt gọi/token AI và đánh giá bằng LLM riêng. Token của bộ chấm không tính vào token pipeline. Đây không phải kiểm thử tải hoặc chấm bởi chuyên gia; câu hỏi tổng hợp số liệu chưa được kiểm định bởi bộ đo này.

## Lộ trình tiếp theo

Đã làm trong v4: chunk theo token và cấu trúc có số trang PDF, embedding kèm ngữ cảnh văn bản, điểm tóm tắt văn bản, bộ lọc đẩy xuống trước truy xuất, truy xuất đa khía cạnh có hạn mức, gom tệp và loại bản trùng, trích quan hệ văn bản, Aggregation v2.

Còn lại:

1. **Đo lại**: chạy benchmark ghép cặp trên index v4; mở rộng bộ câu hỏi lên 100–300 câu được cán bộ xác nhận, có câu về bảng, nhiều trang, nhiều văn bản, số âm/thập phân/phần trăm và expected facts cho tổng hợp số liệu.
2. **Kích thước chunk**: so sánh 250–350 / 400–600 / 700–900 token trên cùng snapshot bằng Hit@k, MRR, grounded claim ratio và độ trễ trước khi đổi `CHUNK_TOKENS`.
3. **Bảng biểu**: tách bảng thành chunk riêng theo nhóm dòng kèm tiêu đề cột; lưu vị trí ký tự để trích dẫn đúng đoạn gốc; số trang cho DOCX.
4. **Rerank hai tầng** (chọn văn bản trước, đoạn sau) và ngưỡng riêng theo loại câu hỏi; negative mining từ log truy vấn thật.
5. **Quan hệ văn bản**: màn hình để cán bộ xác minh quan hệ trích tự động, dùng làm căn cứ kết luận hiệu lực.

## An toàn dữ liệu

- Nội dung dùng cho metadata, trả lời, kiểm chứng và âm thanh có thể được gửi tới OpenRouter hoặc Gemini theo cấu hình. Chỉ dùng với dữ liệu được phép gửi ra dịch vụ bên ngoài.
- Tài khoản QLVB/captcha chỉ dùng cho phiên crawler và không được ghi vào checkpoint.
- Trang quản trị dùng HTTP Basic Auth và **bị khóa nếu ADMIN_PASS chưa đặt, dùng giá trị mặc định hoặc ngắn hơn 10 ký tự**. Đăng nhập sai nhiều lần bị khóa tạm theo IP (`ADMIN_MAX_FAILED_LOGINS`, `ADMIN_LOCKOUT_SECONDS`); đặt `TRUST_PROXY_HEADERS=true` khi chạy sau Cloudflare Tunnel. Nên đặt Cloudflare Access trước khi public domain.
- Đổi QDRANT_API_KEY và mật khẩu PostgreSQL trước khi mở dịch vụ qua Internet; run.sh cảnh báo khi còn giá trị mặc định.
- Tệp tải lên bị giới hạn định dạng và dung lượng (`UPLOAD_MAX_MB`, `AUDIO_MAX_MB`), xử lý với số luồng giới hạn (`UPLOAD_CONCURRENCY`); OCR giới hạn số trang và gửi theo lô.
- Lỗi nội bộ được ghi log, không trả chi tiết cho người dùng. Markdown từ model được làm sạch (DOMPurify) trước khi hiển thị; ứng dụng gửi các header bảo mật cơ bản.
- Tệp âm thanh tải lên Gemini được xóa cả khi gỡ băng lỗi.

## Cấu trúc thư mục

~~~text
src/agent/              Planner, routing, controller (pipeline sự kiện), verifier, công cụ tổng hợp
src/crawler/            Playwright crawler và SQLite checkpoint
src/services/           Ingest theo văn bản, chunking, retrieval, relations, numbers, aggregate
src/templates/          Giao diện chính và quản trị (HTML)
src/static/             CSS/JS của giao diện, PWA (manifest, service worker, icon, offline)
db/migrations/          Migration có checksum
scripts/                Reindex, baseline, benchmark và công cụ vận hành
tests/                  Unit test (chạy: ./venv/bin/python -m unittest discover -s tests)
reports/                Baseline đã lưu
data/                   PostgreSQL, Qdrant và dữ liệu cục bộ khi được cấu hình
~~~
