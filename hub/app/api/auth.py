"""인증 API: 로그인 + 현재 사용자."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core import auth
from app.db import crud

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginBody) -> dict:
    user = await run_in_threadpool(crud.get_user, body.username)
    if user is None or not auth.verify_password(body.password, user.salt, user.pw_hash):
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")
    token = auth.create_token(user.username, user.role, user.scope_type, user.scope_ref)
    return {
        "token": token,
        "user": {
            "username": user.username,
            "role": user.role,
            "scope_type": user.scope_type,
            "scope_ref": user.scope_ref,
        },
    }


@router.get("/me")
async def me(principal: auth.Principal = Depends(auth.require_user)) -> dict:
    return {
        "username": principal.username,
        "role": principal.role,
        "scope_type": principal.scope_type,
        "scope_ref": principal.scope_ref,
    }


class CreateUserBody(BaseModel):
    username: str
    password: str
    role: str = "viewer"            # admin | viewer
    scope_type: str = "cluster"     # cluster | namespace
    scope_ref: str = ""             # cluster: "<cluster_id>"(또는 전체="") · namespace: "<cluster_id>/<ns>"


@router.post("/users")
async def create_user(
    body: CreateUserBody, _: auth.Principal = Depends(auth.require_admin)
) -> dict:
    """웹 사용자 생성(admin 전용). role/scope 지정으로 전체·클러스터·네임스페이스 제한."""
    if body.role not in ("admin", "viewer") or body.scope_type not in ("cluster", "namespace"):
        raise HTTPException(400, "role/scope_type 값 오류")
    if await run_in_threadpool(crud.get_user, body.username) is not None:
        raise HTTPException(409, "이미 존재하는 사용자")
    salt = auth.make_salt()
    await run_in_threadpool(
        crud.create_user, body.username, auth.hash_password(body.password, salt), salt,
        body.role, body.scope_type, body.scope_ref,
    )
    return {"ok": True, "username": body.username, "role": body.role,
            "scope_type": body.scope_type, "scope_ref": body.scope_ref}


@router.get("/users")
async def list_users(_: auth.Principal = Depends(auth.require_admin)) -> list[dict]:
    users = await run_in_threadpool(crud.list_users)
    return [{"username": u.username, "role": u.role, "scope_type": u.scope_type,
             "scope_ref": u.scope_ref} for u in users]


class UpdateUserBody(BaseModel):
    password: str | None = None
    role: str | None = None
    scope_type: str | None = None
    scope_ref: str | None = None


@router.patch("/users/{username}")
async def update_user(
    username: str, body: UpdateUserBody, _: auth.Principal = Depends(auth.require_admin)
) -> dict:
    target = await run_in_threadpool(crud.get_user, username)
    if target is None:
        raise HTTPException(404, "사용자 없음")
    if body.role is not None and body.role not in ("admin", "viewer"):
        raise HTTPException(400, "role 값 오류")
    # 마지막 admin 을 viewer 로 강등 방지(lockout).
    if body.role == "viewer" and target.role == "admin":
        if await run_in_threadpool(crud.count_admins) <= 1:
            raise HTTPException(409, "마지막 admin 은 강등할 수 없습니다")
    pw_hash = salt = None
    if body.password:
        salt = auth.make_salt()
        pw_hash = auth.hash_password(body.password, salt)
    await run_in_threadpool(
        crud.update_user, username, role=body.role, scope_type=body.scope_type,
        scope_ref=body.scope_ref, pw_hash=pw_hash, salt=salt,
    )
    return {"ok": True, "username": username}


@router.delete("/users/{username}")
async def delete_user(
    username: str, principal: auth.Principal = Depends(auth.require_admin)
) -> dict:
    if username == principal.username:
        raise HTTPException(400, "자기 자신은 삭제할 수 없습니다")
    target = await run_in_threadpool(crud.get_user, username)
    if target is None:
        raise HTTPException(404, "사용자 없음")
    if target.role == "admin" and await run_in_threadpool(crud.count_admins) <= 1:
        raise HTTPException(409, "마지막 admin 은 삭제할 수 없습니다")
    await run_in_threadpool(crud.delete_user, username)
    return {"ok": True, "deleted": username}
