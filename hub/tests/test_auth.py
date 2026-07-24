"""인증: 비밀번호 해시 + 서명 토큰 round-trip."""

import os

os.environ["CANGGU_JWT_SECRET"] = "test-secret"

from app.core import auth  # noqa: E402


def test_password_hash_roundtrip():
    salt = auth.make_salt()
    h = auth.hash_password("s3cret", salt)
    assert auth.verify_password("s3cret", salt, h)
    assert not auth.verify_password("wrong", salt, h)


def test_token_roundtrip():
    tok = auth.create_token("alice", "admin", "cluster", "")
    p = auth.verify_token(tok)
    assert p is not None
    assert p.username == "alice" and p.is_admin


def test_token_tampered_rejected():
    tok = auth.create_token("bob", "viewer", "cluster", "")
    assert auth.verify_token(tok + "x") is None
    assert auth.verify_token("garbage") is None


def test_viewer_not_admin():
    p = auth.verify_token(auth.create_token("v", "viewer", "namespace", "c1/team-a"))
    assert p is not None and not p.is_admin and p.scope_ref == "c1/team-a"
