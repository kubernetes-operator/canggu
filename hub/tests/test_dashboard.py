"""대시보드 묶음 API · Server-Timing · 정적 자산 캐시 헤더."""

import asyncio
import json
from urllib.parse import urlencode

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.agentlink.manager import hub
from app.core import auth
from app.db import crud, models
from app.db.models import Cluster
from tests.test_operating import _snap


class _Resp:
    def __init__(self, status, headers, body):
        self.status_code = status
        self.headers = headers
        self._body = body

    def json(self):
        return json.loads(self._body)


class _Client:
    """httpx 없이 ASGI 앱을 직접 호출하는 최소 테스트 클라이언트."""

    def __init__(self, app):
        self.app = app

    def get(self, path, params=None):
        async def go():
            scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                     "path": path, "raw_path": path.encode(), "root_path": "",
                     "query_string": urlencode(params or {}).encode(), "headers": [],
                     "client": ("t", 1), "server": ("t", 80)}
            out = {"status": 0, "headers": {}, "body": b""}
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    await asyncio.sleep(3600)
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(m):
                if m["type"] == "http.response.start":
                    out["status"] = m["status"]
                    out["headers"] = {k.decode(): v.decode() for k, v in m.get("headers", [])}
                elif m["type"] == "http.response.body":
                    out["body"] += m.get("body", b"")

            await self.app(scope, receive, send)
            return _Resp(out["status"], out["headers"], out["body"])

        return asyncio.run(go())


def _principal(scope_type="cluster", scope_ref=""):
    return auth.Principal(username="alice", role="admin", scope_type=scope_type, scope_ref=scope_ref)


@pytest.fixture
def client(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=eng)
    SL = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(crud, "SessionLocal", SL)
    with SL() as db:
        db.add(Cluster(id="c1", name="c1", cluster_mode="OBSERVE"))
        db.commit()
    monkeypatch.setitem(hub.snapshots, "c1", _snap())
    monkeypatch.setitem(hub.issues, "c1", [])
    main.app.dependency_overrides[auth.require_user] = lambda: _principal()
    yield _Client(main.app)
    main.app.dependency_overrides.clear()


def test_dashboard_bundles_all_sections(client):
    r = client.get("/api/clusters/c1/dashboard", params={"namespace": "a"})
    assert r.status_code == 200
    d = r.json()
    assert d["errors"] == {}
    for k in ("clusters", "namespaces", "namespace_modes", "pods", "services", "workloads",
              "storage", "routes", "events", "operating", "issues", "commands", "summary"):
        assert k in d, k
    assert d["namespaces"] == ["a", "b"]
    assert {p["namespace"] for p in d["pods"]} == {"a"}          # namespace 필터 적용
    # 개별 API 와 같은 결과
    assert d["pods"] == client.get("/api/clusters/c1/pods", params={"namespace": "a"}).json()
    st = r.headers["server-timing"]
    assert st.startswith("app;dur=") and "s-" in st


def test_dashboard_section_error_isolated(client, monkeypatch):
    from app.api import resources

    async def boom(*a, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(resources, "routes", boom)
    d = client.get("/api/clusters/c1/dashboard").json()
    assert d["routes"] is None and d["errors"]["routes"].startswith("500")
    assert d["pods"]                                               # 나머지는 정상


def test_dashboard_scope_enforced(client):
    main.app.dependency_overrides[auth.require_user] = lambda: _principal("namespace", "c1/a")
    assert client.get("/api/clusters/c1/dashboard", params={"namespace": "b"}).status_code == 403


def test_static_cache_headers(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc.js").write_text("x")
    (tmp_path / "index.html").write_text("<html></html>")
    from fastapi import FastAPI

    app = FastAPI()
    app.mount("/", main.CachedStaticFiles(directory=str(tmp_path), html=True))
    c = _Client(app)
    assert "immutable" in c.get("/assets/index-abc.js").headers["cache-control"]
    assert c.get("/").headers["cache-control"] == "no-cache"
