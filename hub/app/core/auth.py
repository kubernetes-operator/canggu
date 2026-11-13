"""인증: 표준 라이브러리만 사용하는 서명 세션 토큰 + 비밀번호 해시 + FastAPI 의존성.

외부 JWT/OIDC 의존성 없이 HMAC-SHA256 서명 토큰을 쓴다(Phase 3 최소 구현).
OIDC 연동은 향후. 비밀번호는 pbkdf2_hmac 로 솔트 해싱.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from app.config import get_settings

_PBKDF2_ITERS = 200_000


# ── 비밀번호 ─────────────────────────────────────────────────────────────────
def make_salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERS).hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected_hash)


# ── 토큰 (HMAC 서명) ─────────────────────────────────────────────────────────
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_token(username: str, role: str, scope_type: str, scope_ref: str) -> str:
    s = get_settings()
    payload = {
        "sub": username,
        "role": role,
        "scope_type": scope_type,
        "scope_ref": scope_ref,
        "exp": int(time.time()) + s.session_ttl_seconds,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(s.jwt_secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


@dataclass
class Principal:
    username: str
    role: str
    scope_type: str
    scope_ref: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def verify_token(token: str) -> Principal | None:
    s = get_settings()
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(s.jwt_secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return Principal(
            username=payload["sub"],
            role=payload.get("role", "viewer"),
            scope_type=payload.get("scope_type", "cluster"),
            scope_ref=payload.get("scope_ref", ""),
        )
    except Exception:  # noqa: BLE001
        return None


# ── FastAPI 의존성 ───────────────────────────────────────────────────────────
def _principal_from_header(authorization: str | None) -> Principal | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return verify_token(authorization[7:])


def require_user(authorization: str | None = Header(default=None)) -> Principal:
    s = get_settings()
    if not s.auth_enabled:
        return Principal("dev", "admin", "cluster", "")  # dev 우회
    p = _principal_from_header(authorization)
    if p is None:
        raise HTTPException(401, "인증 필요")
    return p


def require_admin(principal: Principal = Depends(require_user)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(403, "admin 권한 필요")
    return principal
