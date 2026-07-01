# DocNexus (QLVB AI v3) - Sở Khoa học và Công nghệ

DocNexus là hệ thống trợ lý AI chuyên biệt dành cho nghiệp vụ quản lý, tra cứu và xử lý văn bản hành chính, được phát triển phục vụ Sở Khoa học và Công nghệ. Hệ thống kết hợp các công nghệ truy xuất thông tin hiện đại (RAG, Hybrid Search) và tự động hóa quy trình (RPA) để tối ưu hóa công tác văn thư và thẩm định dự thảo.

## 🏛 Kiến trúc Hệ thống (Architecture)

Sơ đồ tổng quan về luồng dữ liệu (Data Flow) và xử lý truy vấn:

```mermaid
graph TD
    subgraph "1. Thu thập dữ liệu"
        A["Hệ thống QLVB"] -->|"Tải PDF, DOCX, XLSX"| B("RPA Crawler (Playwright)")
    end

    B --> C

    subgraph "2. Xử lý và Nhúng (Ingest)"
        C["1. Trích xuất text/OCR"] --> D["2. LLM trích xuất Meta"]
        D --> E["3. Chunking đệ quy"]
        E --> F["4. Embed Dense và Sparse"]
    end

    F -->|"Lưu Doc-level và Meta"| G[("PostgreSQL DB")]
    F -->|"Lưu Chunks và Vector"| H[("Qdrant Vector DB")]

    subgraph "3. Truy xuất và Trả lời"
        G -.->|"Fuzzy match / Filter"| I
        H -.->|"Vector Search"| I{"Hybrid Search và RRF"}
        I -->|"Top K"| J["Reranking: BGE-Reranker"]
        J --> K["Định tuyến LLM"]
    end

    K -->|"Trả kết quả"| L(["Client"])
    
    style G fill:#3B9EFF,stroke:#1E6FE0,stroke-width:2px,color:#fff
    style H fill:#35C28A,stroke:#059669,stroke-width:2px,color:#fff
```

Hệ thống được thiết kế theo kiến trúc Micro-services thu nhỏ, phân tách rõ ràng giữa lớp thu thập dữ liệu, lưu trữ, xử lý AI và giao diện người dùng:

1. **Data Ingestion & RPA Pipeline:** Sử dụng Playwright để tự động cào dữ liệu (Văn bản đi/đến), vượt qua SSO. Dữ liệu tải về qua bộ lọc 4 lớp và được trích xuất nội dung đa định dạng.
2. **Metadata & Chunking:** LLM cấu hình nhẹ đóng vai trò bóc tách metadata. Văn bản được chia chunk đệ quy để bảo toàn cấu trúc ngữ nghĩa (Điều, Khoản).
3. **Dual Storage (Lưu trữ kép):** Postgres đóng vai trò *System of Record*, Qdrant lưu trữ vector để tìm kiếm.
4. **Retrieval & RAG Pipeline:** Áp dụng Hybrid Search cùng thuật toán RRF. Kết quả sau đó được chấm điểm và sắp xếp lại bằng Cross-Encoder (Reranker).
5. **Presentation Layer:** Giao diện Web tối giản, tốc độ cao (FastAPI + Jinja2).

## 🛠 Công nghệ sử dụng (Tech Stack)
- **Backend & Core:** FastAPI, Uvicorn, Python 3.
- **Database:** PostgreSQL (`pg_trgm`), Qdrant.
- **AI & Machine Learning:** `BAAI/bge-m3`, `BAAI/bge-reranker-v2-m3`, OpenRouter/Gemini API.
- **Data Extraction:** `PyMuPDF`, `python-docx`, `pandas`, `pytesseract`, `pdf2image`, LibreOffice.
- **RPA & Crawler:** Playwright (async).
- **Frontend:** HTML5, CSS3, Vanilla JS, SweetAlert2.

## ✨ Các tính năng nổi bật (Key Features)

### 1. Tra cứu văn bản thông minh (Hybrid Search + RAG)
Tìm kiếm nội dung văn bản bằng ngôn ngữ tự nhiên. Trợ lý AI đọc các đoạn văn bản liên quan và tổng hợp câu trả lời, bắt buộc trích dẫn nguồn minh chứng.

### 2. Tổng hợp số liệu đa văn bản (Map-Reduce)
Khắc phục nhược điểm "ảo giác toán học" của LLM bằng luồng Map-Reduce chuyên biệt:

```mermaid
graph LR
    A(["Câu hỏi đếm/cộng"]) --> B["LLM: Lập Kế hoạch"]
    B -->|"Lọc Loại VB, Thời gian"| C[("Kho Văn bản")]
    C -->|"Duyệt từng VB"| D["LLM: Trích xuất số liệu"]
    D -->|"Map"| E["Code Python: Cộng/Đếm"]
    E -->|"Reduce"| F(["Báo cáo Tổng và Minh chứng"])
    
    style E fill:#E8A93B,stroke:#D97706,stroke-width:2px,color:#fff
```

### 3. Kiểm tra, thẩm định dự thảo (Draft Checking)
- **Kiểm tra thể thức:** Rà soát các lỗi định dạng (font, lề, cỡ chữ) tự động bằng code Python dựa trên NĐ 30/2020/NĐ-CP.
- **Kiểm tra nội dung:** Phát hiện lỗi logic, chính tả và kiểm tra chéo (cross-check) tính hợp lệ của các căn cứ pháp lý.

### 4. Tự động hóa cào dữ liệu (Auto-Crawler) & Admin Dashboard
Cào tự động văn bản Đi/Đến, tự động mở cửa sổ xử lý Captcha và giám sát các luồng tải ngay trên Dashboard thời gian thực.