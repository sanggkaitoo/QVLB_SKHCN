# DocNexus (QLVB AI v3)

DocNexus là web app hỗ trợ cán bộ, công chức và viên chức tra cứu, tổng hợp và kiểm tra văn bản hành chính. Hệ thống lưu dữ liệu gốc trong PostgreSQL, lập chỉ mục vector trên Qdrant, sử dụng RAG để trả lời có nguồn và dùng Playwright để thu thập văn bản Đi/Đến từ hệ thống QLVB.

> Trạng thái hiện tại: RAG truyền thống đang là luồng mặc định. Agentic RAG v2 đã có endpoint riêng và feature flag, nhưng chưa nên bật mặc định cho đến khi re-index đầy đủ và đo lại trên bộ đánh giá lớn hơn. Tính năng tổng hợp số liệu hiện ở mức thử nghiệm.

## Chức năng hiện có

### Tra cứu và hỏi đáp văn bản

- Tìm kiếm hybrid bằng vector dense, sparse và Reciprocal Rank Fusion (RRF).
- Rerank kết quả bằng BAAI/bge-reranker-v2-m3.
- Tìm chính xác theo số ký hiệu từ PostgreSQL.
- Lọc theo loại văn bản, hướng Đi/Đến, thời gian và cơ quan ban hành trong luồng Agentic.
- Trả lời dựa trên bằng chứng, kèm nguồn và mức độ tin cậy.

### Agentic RAG v2

- Phân loại ý định: tra cứu chính xác, hỏi đáp ngữ nghĩa, so sánh, tổng hợp và hiệu lực pháp lý.
- Lập kế hoạch truy vấn có cấu trúc và tạo nhiều truy vấn tìm kiếm.
- Mở rộng chunk lân cận và section cha.
- Tìm quan hệ căn cứ, sửa đổi, thay thế và bãi bỏ giữa các văn bản.
- Thử lại có giới hạn, kiểm chứng câu trả lời và từ chối khi thiếu bằng chứng.
- Ghi log kế hoạch, nguồn, điểm rerank, số vòng và thời gian xử lý.
- Có endpoint A/B riêng để so sánh với RAG cũ.

### Tổng hợp số liệu

Luồng hiện tại lập kế hoạch, chọn văn bản ứng viên, dùng LLM trích một giá trị từ từng văn bản rồi cộng hoặc đếm bằng code. Kết quả có bảng minh chứng theo văn bản.

Tính năng này đang thử nghiệm và chưa phù hợp để dùng như số liệu quyết toán hoặc báo cáo chính thức nếu chưa kiểm tra lại bằng chứng. Các giới hạn hiện tại:

- Chỉ xét tối đa 60 văn bản ứng viên.
- Mỗi văn bản chỉ đọc 8.000 ký tự đầu.
- Mỗi văn bản chỉ trả về một giá trị, nên dễ bỏ sót bảng hoặc nhiều dòng số liệu.
- Chưa chuẩn hóa đầy đủ đơn vị như đồng, nghìn đồng, triệu đồng, tỷ đồng và phần trăm.
- Chưa phân biệt chắc chắn số kế hoạch, số thực hiện, số lũy kế và tổng cộng.
- Chưa có cơ chế chống cộng trùng giữa dòng chi tiết và dòng tổng.

### Thu thập và nhập dữ liệu

- Crawl Văn bản Đi, Văn bản Đến hoặc toàn bộ hệ thống QLVB.
- Đăng nhập SSO qua captcha trên trang quản trị.
- Dùng nhiều locator dự phòng, thử lại khi điều hướng chậm và tự đăng nhập lại khi phiên hết hạn.
- Tự nhận diện cột theo tiêu đề bảng và kiểm soát lỗi phân trang.
- Checkpoint bằng SQLite, có thể tiếp tục sau khi dừng hoặc gặp lỗi.
- Nhận diện văn bản theo số ký hiệu kết hợp ngày ban hành.
- Tải và ingest theo lô để số file đang mở không tăng theo tổng số văn bản.
- Giữ bản gốc đã ingest trong data/store và chống nạp trùng bằng SHA-256.
- Hỗ trợ PDF, DOC/DOCX, XLS/XLSX, CSV và OCR ảnh/PDF scan.

### Tiện ích nghiệp vụ

- Kiểm tra thể thức và nội dung dự thảo văn bản.
- OCR tài liệu qua máy chủ Unlimited-OCR/SGLang tùy chọn.
- Gỡ băng âm thanh bằng Gemini và tạo bản tóm tắt có cấu trúc.
- Trang quản trị hiển thị thống kê, danh sách văn bản và trạng thái crawler.
- Giao diện responsive, có chế độ sáng/tối.
- PWA có offline fallback và có thể thêm vào màn hình chính.

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
./venv/bin/pip install -r requirements.txt
PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright" ./venv/bin/playwright install chromium
cp .env.example .env
~~~

Sửa .env trước khi chạy. Tối thiểu cần cấu hình khóa LLM và thay toàn bộ mật khẩu mặc định.

~~~env
OPENROUTER_API_KEY=...
ADMIN_USER=admin
ADMIN_PASS=mat-khau-manh
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

Mở /admin, đăng nhập bằng ADMIN_USER và ADMIN_PASS, chọn phạm vi Đi/Đến/toàn bộ rồi bắt đầu crawl. Khi giao diện hiển thị captcha SSO, nhập tài khoản QLVB và captcha hiện tại.

Các cấu hình quan trọng:

~~~env
CRAWLER_BATCH_SIZE=20
CRAWLER_NAVIGATION_RETRIES=3
CRAWLER_LOGIN_RETRIES=5
CRAWLER_PAGE_TIMEOUT_SECONDS=90
CRAWLER_LOGIN_INPUT_TIMEOUT_SECONDS=600
CRAWLER_MAX_PAGES=0
~~~

CRAWLER_MAX_PAGES=0 nghĩa là không giới hạn số trang. Checkpoint nằm tại CRAWLER_STATE_DB. Lịch sử JSON cũ được nhập tự động; do lịch sử cũ không có ngày ban hành, một số văn bản có thể được kiểm tra lại một lần để tránh bỏ sót văn bản trùng số ở năm khác.

Docker đặt nofile của PostgreSQL và Qdrant ở 262144. Crawler xử lý theo lô và đóng tài nguyên sau từng tệp, nên số file mở được giữ ổn định thay vì tăng theo số lượng tài liệu. Với kho rất lớn, giới hạn thực tế còn phụ thuộc dung lượng đĩa, tốc độ QLVB, OCR, embedding và hạn mức LLM.

## Agentic RAG

### Endpoint và feature flag

| Cấu hình | Ý nghĩa |
|---|---|
| /api/search_stream | Endpoint chính; dùng Agentic khi feature flag được bật |
| /api/search_agent_stream | Endpoint Agentic để A/B test trực tiếp |
| AGENTIC_RAG_ENABLED=false | Giữ RAG cũ làm mặc định |
| RAG_COLLECTION=qlvb_docs_v2 | Collection structured chunks |
| AGENT_MAX_ATTEMPTS=2 | Số vòng retrieval tối đa |

Re-index sang collection mới, không ghi đè collection cũ:

~~~bash
./venv/bin/python scripts/reindex_v2.py --limit 1
./venv/bin/python scripts/reindex_v2.py --collection qlvb_docs_v2
~~~

Chỉ chuyển cấu hình sau khi re-index hoàn tất và đã so sánh số văn bản/vector:

~~~env
RAG_COLLECTION=qlvb_docs_v2
AGENTIC_RAG_ENABLED=true
~~~

Rollback không cần xóa dữ liệu:

~~~env
AGENTIC_RAG_ENABLED=false
~~~

Hướng dẫn vận hành chi tiết nằm tại [docs/agentic-rag.md](docs/agentic-rag.md).

## Đánh giá chất lượng

Baseline trước nâng cấp được lưu tại [reports/baseline-v1.md](reports/baseline-v1.md), kết quả Agentic hiện tại tại [reports/baseline-agent-v2.md](reports/baseline-agent-v2.md).

Trên bộ pilot 10 câu hỏi hiện có:

| Chỉ số | RAG v1 | Agentic v2 |
|---|---:|---:|
| Hit@1 | 25,0% | 75,0% |
| Hit@5 | 50,0% | 87,5% |
| Hit@10 | 62,5% | 100,0% |
| MRR | 0,37 | 0,807 |
| Grounded claim ratio | 14,3% | 70,0% |
| P95 latency | 13,8 giây | 66,1 giây |

Bộ pilot còn nhỏ và độ trễ Agentic còn cao, vì vậy đây chưa phải bằng chứng đủ để bật mặc định trong production.

Chạy lại đánh giá retrieval:

~~~bash
./venv/bin/python scripts/run_agent_retrieval_baseline.py
~~~

Đo đầy đủ qua API và tạo báo cáo:

~~~bash
./venv/bin/python scripts/run_baseline_api.py   --endpoint /api/search_agent_stream   --output reports/generated/baseline-agent-v2.jsonl   --judge --overwrite

./venv/bin/python scripts/report_baseline.py   --input reports/generated/baseline-agent-v2.jsonl   --output reports/generated/baseline-agent-v2.md
~~~

## Lộ trình cải thiện độ chính xác

### 1. Đo chất lượng chunk trước khi đổi kích thước

Giả thuyết chunk quá ngắn hoặc quá dài là hợp lý, nhưng không nên chỉ thay CHUNK_SIZE rồi đánh giá bằng cảm giác. Cần tạo các collection thử nghiệm từ cùng một snapshot dữ liệu:

| Biến thể | Child chunk | Parent context | Overlap |
|---|---:|---:|---:|
| A | 250-350 token | 1.500 token | 10% |
| B | 400-600 token | 2.000 token | 12-15% |
| C | 700-900 token | 3.000 token | 10% |

So sánh theo từng nhóm câu hỏi bằng Hit@k, Recall@k, MRR, tỷ lệ nguồn đúng, grounded claim ratio, độ trễ và chi phí. Chọn cấu hình theo kết quả đo, không chọn một kích thước chung cho mọi loại tài liệu.

### 2. Chunk theo cấu trúc và loại nội dung

- Giữ ranh giới Chương, Mục, Điều, Khoản và Điểm; không overlap qua hai Điều khác nhau.
- Gắn tiêu đề Điều/Khoản, số ký hiệu, loại văn bản, ngày và cơ quan vào nội dung dùng để embedding.
- Tách bảng thành chunk riêng theo hàng/nhóm hàng nhưng giữ tiêu đề cột trong từng chunk.
- Lưu số trang và vị trí ký tự để trích dẫn đúng đoạn gốc.
- Dùng child chunk nhỏ để tìm kiếm, sau đó lấy parent section và chunk lân cận để trả lời.
- Tạo representation riêng cho trích yếu/metadata, nội dung pháp lý và bảng số liệu thay vì dùng một vector cho mọi mục đích.

Hiện tại structured chunker đã nhận diện Chương/Mục/Điều/Khoản, nhưng kích thước vẫn tính theo ký tự và page_start/page_end chưa có dữ liệu thực. Đây là phần nên ưu tiên nâng cấp tiếp theo.

### 3. Nâng retrieval và rerank

- Đẩy bộ lọc ngày và cơ quan vào Qdrant/PostgreSQL trước retrieval thay vì lọc sau khi lấy candidate.
- Dùng candidate pool thích ứng theo intent; câu hỏi so sánh và tổng hợp cần nhiều văn bản hơn câu hỏi tra cứu chính xác.
- Rerank theo hai tầng: chọn văn bản trước, chọn passage trong từng văn bản sau.
- Hiệu chỉnh ngưỡng điểm riêng cho exact lookup, semantic QA, compare và legal status.
- Đảm bảo đa dạng văn bản trong top-k nhưng cho phép lấy nhiều đoạn khi câu hỏi yêu cầu một văn bản cụ thể.
- Bổ sung negative mining từ log truy vấn thật để tinh chỉnh reranker cho ngôn ngữ hành chính Việt Nam.

### 4. Xây Aggregation v2

Luồng tổng hợp mới nên coi LLM là bộ trích xuất dữ kiện, không phải máy tính và cũng không phải bộ chọn toàn bộ tập dữ liệu:

1. Planner tạo schema có kiểu: chỉ số, phép tính, đơn vị, kỳ báo cáo, phạm vi, trạng thái kế hoạch/thực hiện và group_by.
2. PostgreSQL liệt kê đầy đủ văn bản thuộc phạm vi bằng cursor, không giới hạn cứng 60.
3. Retrieval lấy các chunk liên quan trong toàn bộ văn bản, không cắt full_text ở 8.000 ký tự đầu.
4. Extractor trả về nhiều fact có cấu trúc từ đoạn văn và bảng, mỗi fact gắn doc_id, trang, section, đoạn bằng chứng và confidence.
5. Bộ chuẩn hóa đổi nghìn/triệu/tỷ, dấu phân cách Việt Nam, phần trăm, khoảng giá trị và đơn vị thời gian về dạng chuẩn.
6. Bộ chống trùng nhận diện dòng tổng, dòng chi tiết, số lũy kế và số của riêng kỳ báo cáo.
7. Python hoặc SQL thực hiện sum, count, distinct_count, avg, min, max và nhóm dữ liệu.
8. Verifier đối chiếu tổng với từng fact, kiểm tra đơn vị và các bất biến số học trước khi trả kết quả.
9. Fact mâu thuẫn hoặc confidence thấp được đưa vào danh sách cần cán bộ xác nhận, không tự cộng vào kết quả chính.

### 5. Bộ đánh giá cần bổ sung

- Tăng từ 10 câu pilot lên ít nhất 100-300 câu đã được cán bộ xác nhận.
- Có câu hỏi chứa bảng, nhiều trang, nhiều văn bản, số âm, số thập phân, phần trăm và đơn vị khác nhau.
- Đo riêng document recall, passage recall, độ chính xác giá trị, độ chính xác đơn vị, độ đầy đủ tập văn bản và sai số tổng cuối.
- Lưu expected facts thay vì chỉ expected document để biết lỗi nằm ở retrieval, extraction, normalization hay reduce.
- Chỉ rollout khi chất lượng tăng trên cùng bộ test và độ trễ vẫn trong ngưỡng sử dụng thực tế.

## An toàn dữ liệu

- Nội dung dùng cho metadata, trả lời, kiểm chứng và âm thanh có thể được gửi tới OpenRouter hoặc Gemini theo cấu hình.
- Chỉ dùng với dữ liệu được phép gửi ra dịch vụ bên ngoài.
- Tài khoản QLVB/captcha chỉ dùng cho phiên crawler và không được ghi vào checkpoint.
- Đổi ADMIN_PASS, QDRANT_API_KEY và mật khẩu PostgreSQL trước khi mở dịch vụ qua Internet.
- Trang quản trị dùng HTTP Basic Auth; nên đặt Cloudflare Access hoặc một lớp SSO phía trước khi public domain.

## Cấu trúc thư mục

~~~text
src/agent/              Agent controller, planner và verifier
src/crawler/            Playwright crawler và SQLite checkpoint
src/services/           Ingest, chunking, retrieval, aggregate
src/templates/          Giao diện chính và quản trị
src/static/pwa/         Manifest, service worker, icon và offline page
db/migrations/          Migration có checksum
scripts/                Re-index, baseline và công cụ vận hành
reports/                Baseline đã lưu
data/                   PostgreSQL, Qdrant và dữ liệu cục bộ khi được cấu hình
~~~
