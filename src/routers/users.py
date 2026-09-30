"""Quản lý tài khoản: thêm, sửa, khoá, xoá; vai trò và quyền; nhật ký thao tác.

Quy tắc phân cấp:
  - Super admin quản lý mọi tài khoản (kể cả super admin khác).
  - Quản trị viên chỉ tạo/sửa/xoá tài khoản có vai trò thấp hơn mình và không tự sửa vai trò của mình.
  - Hệ thống luôn giữ ít nhất một super admin đang hoạt động.
"""
import asyncio
import re
from typing import Literal

import psycopg2.errors
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.core import security, store
from src.core.security import ROLE_PERMISSIONS, ROLE_RANK, ROLES, User

router = APIRouter()
Role = Literal["super_admin", "admin", "operator", "staff", "viewer"]
_USERNAME = re.compile(r"^[A-Za-z0-9._@-]{3,50}$")
_manager = security.require("users.manage")

ROLE_DESCRIPTIONS = {
    "super_admin": "Toàn quyền: cấu hình AI và khoá API, quản lý mọi tài khoản kể cả quản trị viên.",
    "admin": "Quản trị hệ thống: crawler, kho văn bản, tài khoản cấp dưới; xem cấu hình AI.",
    "operator": "Vận hành dữ liệu: chạy crawler/đồng bộ QLVB, xem kho văn bản; dùng mọi công cụ.",
    "staff": "Chuyên viên: dùng mọi công cụ AI và được chọn mô hình khi tra cứu.",
    "viewer": "Chỉ tra cứu văn bản bằng mô hình mặc định.",
}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=1, max_length=500)
    full_name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=200)
    role: Role = "staff"
    daily_quota: int = Field(0, ge=0, le=100_000)
    must_change_password: bool = True
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=200)
    role: Role | None = None
    daily_quota: int | None = Field(None, ge=0, le=100_000)
    is_active: bool | None = None
    must_change_password: bool | None = None
    password: str | None = Field(None, max_length=500)  # đặt lại mật khẩu


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


@router.get("/roles")
async def api_roles(user: User = Depends(_manager)):
    return {"roles": [{"key": key, "label": label, "description": ROLE_DESCRIPTIONS[key],
                       "permissions": sorted(ROLE_PERMISSIONS[key]), "assignable": security.can_manage(user, key)}
                      for key, label in sorted(ROLES.items(), key=lambda item: -ROLE_RANK[item[0]])],
            "permissions": security.PERMISSION_LABELS}


def _list_users(q: str) -> list[dict]:
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """SELECT u.id, u.username, u.full_name, u.email, u.role, u.is_active, u.daily_quota,
                      u.must_change_password, u.created_at, u.last_login_at, c.username AS created_by,
                      (SELECT count(*) FROM rag_query_logs l WHERE l.user_id = u.id
                        AND l.created_at >= date_trunc('day', now())) AS used_today,
                      (SELECT count(*) FROM user_sessions s WHERE s.user_id = u.id AND s.expires_at > now()) AS sessions
               FROM users u LEFT JOIN users c ON c.id = u.created_by
               WHERE %s = '' OR u.username ILIKE %s OR u.full_name ILIKE %s OR u.email ILIKE %s
               ORDER BY u.is_active DESC, CASE u.role WHEN 'super_admin' THEN 0 WHEN 'admin' THEN 1
                        WHEN 'operator' THEN 2 WHEN 'staff' THEN 3 ELSE 4 END, lower(u.username)""",
            (q, f"%{q}%", f"%{q}%", f"%{q}%"))
        return [dict(row) for row in cursor.fetchall()]


@router.get("")
async def api_users(q: str = Query("", max_length=100), user: User = Depends(_manager)):
    rows = await asyncio.to_thread(_list_users, q.strip())
    for row in rows:
        row["role_label"] = ROLES.get(row["role"], row["role"])
        row["manageable"] = security.can_manage(user, row["role"], row["id"])
        row["is_self"] = row["id"] == user.id
    return {"users": rows}


def _get_row(user_id: int) -> dict:
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute("SELECT id, username, role, is_active FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy tài khoản.")
    return dict(row)


def _other_active_super_admins(user_id: int) -> int:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM users WHERE role = 'super_admin' AND is_active AND id <> %s", (user_id,))
        return int(cursor.fetchone()[0])


@router.post("")
async def api_create_user(req: UserCreate, request: Request, user: User = Depends(_manager)):
    username = req.username.strip()
    if not _USERNAME.match(username):
        raise HTTPException(400, "Tên đăng nhập 3–50 ký tự, chỉ gồm chữ không dấu, số và . _ @ -")
    if not security.can_manage(user, req.role):
        raise HTTPException(403, "Bạn không được tạo tài khoản với vai trò này.")
    problem = security.password_problem(req.password, username)
    if problem:
        raise HTTPException(400, problem)

    def insert() -> int:
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO users (username, full_name, email, password_hash, role, is_active, daily_quota,
                                      must_change_password, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (username, _clean(req.full_name), _clean(req.email), security.hash_password(req.password), req.role,
                 req.is_active, req.daily_quota, req.must_change_password, user.id))
            return int(cursor.fetchone()[0])

    try:
        new_id = await asyncio.to_thread(insert)
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Tên đăng nhập đã tồn tại.")
    security.audit(user, "user_created", username, {"role": req.role}, security.client_ip(request))
    return {"id": new_id}


@router.patch("/{user_id}")
async def api_update_user(user_id: int, req: UserUpdate, request: Request, user: User = Depends(_manager)):
    target = await asyncio.to_thread(_get_row, user_id)
    is_self = target["id"] == user.id
    # Tự sửa: chỉ thông tin cá nhân (vai trò, khoá tài khoản, hạn mức do cấp trên quyết định).
    if is_self and (req.role not in (None, target["role"]) or req.is_active is False
                    or (req.daily_quota is not None and user.role != "super_admin")):
        raise HTTPException(400, "Không thể tự đổi vai trò, tự khoá hoặc tự đổi hạn mức của chính mình.")
    if not is_self and not security.can_manage(user, target["role"], target["id"]):
        raise HTTPException(403, "Bạn không có quyền sửa tài khoản này.")
    if req.role and not is_self and not security.can_manage(user, req.role):
        raise HTTPException(403, "Bạn không được gán vai trò này.")
    demoting = target["role"] == "super_admin" and ((req.role and req.role != "super_admin") or req.is_active is False)
    if demoting and await asyncio.to_thread(_other_active_super_admins, target["id"]) == 0:
        raise HTTPException(400, "Phải còn ít nhất một super admin đang hoạt động.")
    fields: dict = {}
    if req.full_name is not None:
        fields["full_name"] = _clean(req.full_name)
    if req.email is not None:
        fields["email"] = _clean(req.email)
    for name in ("role", "daily_quota", "is_active", "must_change_password"):
        value = getattr(req, name)
        if value is not None:
            fields[name] = value
    if req.password:
        problem = security.password_problem(req.password, target["username"])
        if problem:
            raise HTTPException(400, problem)
        fields["password_hash"] = security.hash_password(req.password)
    if not fields:
        return {"ok": True}

    def update():
        assignments = ", ".join(f"{name} = %s" for name in fields)
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute(f"UPDATE users SET {assignments}, updated_at = now() WHERE id = %s",
                           (*fields.values(), user_id))
        # Đổi mật khẩu, vai trò hoặc khoá tài khoản: đăng xuất các phiên cũ của người đó.
        if not is_self and ({"password_hash", "role"} & fields.keys() or fields.get("is_active") is False):
            security.delete_user_sessions(user_id)

    await asyncio.to_thread(update)
    security.forget_session_cache()
    detail = {key: value for key, value in fields.items() if key != "password_hash"}
    if "password_hash" in fields:
        detail["password_reset"] = True
    security.audit(user, "user_updated", target["username"], detail, security.client_ip(request))
    return {"ok": True}


@router.delete("/{user_id}")
async def api_delete_user(user_id: int, request: Request, user: User = Depends(_manager)):
    target = await asyncio.to_thread(_get_row, user_id)
    if target["id"] == user.id:
        raise HTTPException(400, "Không thể tự xoá tài khoản của chính mình.")
    if not security.can_manage(user, target["role"], target["id"]):
        raise HTTPException(403, "Bạn không có quyền xoá tài khoản này.")
    if target["role"] == "super_admin" and await asyncio.to_thread(_other_active_super_admins, target["id"]) == 0:
        raise HTTPException(400, "Phải còn ít nhất một super admin đang hoạt động.")

    def delete():
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))

    await asyncio.to_thread(delete)
    security.forget_session_cache()
    security.audit(user, "user_deleted", target["username"], {"role": target["role"]}, security.client_ip(request))
    return {"ok": True}


def _audit_rows(limit: int) -> list[dict]:
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute("SELECT created_at, username, action, target, detail, ip FROM audit_log "
                       "ORDER BY created_at DESC LIMIT %s", (limit,))
        return [dict(row) for row in cursor.fetchall()]


@router.get("/audit")
async def api_audit(limit: int = Query(100, ge=1, le=500), _: User = Depends(security.require("audit.view"))):
    return {"items": await asyncio.to_thread(_audit_rows, limit)}
