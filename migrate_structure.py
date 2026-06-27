import os
import shutil

def move_file(src, dest):
    if os.path.exists(src):
        shutil.move(src, dest)
        print(f"✅ Đã di chuyển: {src} -> {dest}")
    else:
        print(f"⚠️ Không tìm thấy (có thể đã di chuyển): {src}")

def main():
    print("🚀 Bắt đầu tái cấu trúc thư mục dự án...\n")

    # 1. Tạo các thư mục mới
    dirs_to_create = [
        "rpa",
        "src/core",
        "src/routers",
        "src/services",
        "src/utils",
        "src/templates"
    ]
    for d in dirs_to_create:
        os.makedirs(d, exist_ok=True)
        print(f"📁 Đã tạo thư mục: {d}")

    print("\n📦 Bắt đầu di chuyển files...")

    # 2. Chuyển files vào src/core/
    move_file("src/config.py", "src/core/config.py")
    move_file("src/llm.py", "src/core/llm.py")
    move_file("src/store.py", "src/core/store.py")
    move_file("src/embedder.py", "src/core/embedder.py")

    # 3. Chuyển files vào src/services/
    move_file("src/ingest.py", "src/services/ingest.py")
    move_file("src/search.py", "src/services/search_srv.py")
    move_file("src/aggregate.py", "src/services/aggregate_srv.py")

    # 4. Chuyển files vào src/utils/
    move_file("src/extract.py", "src/utils/extract.py")
    move_file("src/metadata.py", "src/utils/metadata.py")

    # 5. Chuyển files giao diện vào src/templates/
    move_file("src/index.html", "src/templates/index.html")
    move_file("src/admin.html", "src/templates/admin.html")
    move_file("index.html", "src/templates/index.html") # Đề phòng file nằm ở root
    move_file("admin.html", "src/templates/admin.html") 

    # 6. Chuyển code RPA (nếu có)
    move_file("rpa_logic.py", "rpa/crawler.py")

    # 7. Đổi tên api.py thành main.py (tạm thời để ở src/)
    if os.path.exists("src/api.py"):
        shutil.move("src/api.py", "src/main.py")
        print("✅ Đã đổi tên: src/api.py -> src/main.py")

    print("\n🎉 Hoàn tất phân bổ file! Hãy chuyển sang bước Refactor Import.")

if __name__ == "__main__":
    main()