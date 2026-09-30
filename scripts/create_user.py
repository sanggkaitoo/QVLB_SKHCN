"""Tạo tài khoản hoặc đặt lại mật khẩu từ dòng lệnh (khi chưa vào được trang quản trị).

Ví dụ:
    python scripts/create_user.py admin --role super_admin
    python scripts/create_user.py admin --reset          # đặt lại mật khẩu, mở khoá tài khoản
Mật khẩu được hỏi qua bàn phím (không hiện, không lưu vào lịch sử lệnh).
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import security, store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username")
    parser.add_argument("--role", choices=sorted(security.ROLES), default="super_admin")
    parser.add_argument("--full-name", default=None)
    parser.add_argument("--reset", action="store_true", help="đặt lại mật khẩu cho tài khoản đã có")
    args = parser.parse_args()

    password = getpass.getpass("Mật khẩu: ")
    if password != getpass.getpass("Nhập lại: "):
        print("Hai lần nhập không khớp.")
        return 1
    problem = security.password_problem(password, args.username)
    if problem:
        print(problem)
        return 1

    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM users WHERE lower(username) = lower(%s)", (args.username,))
        row = cursor.fetchone()
        if row and not args.reset:
            print("Tài khoản đã tồn tại; thêm --reset để đặt lại mật khẩu.")
            return 1
        if row:
            cursor.execute("UPDATE users SET password_hash = %s, is_active = TRUE, must_change_password = FALSE, "
                           "updated_at = now() WHERE id = %s", (security.hash_password(password), row[0]))
            cursor.execute("DELETE FROM user_sessions WHERE user_id = %s", (row[0],))
            print(f"Đã đặt lại mật khẩu và mở khoá '{args.username}'.")
        else:
            cursor.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES (%s, %s, %s, %s)",
                           (args.username, args.full_name, security.hash_password(password), args.role))
            print(f"Đã tạo tài khoản '{args.username}' ({security.ROLES[args.role]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
