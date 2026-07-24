"""canggu hub 진입점."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from fastapi import Depends

from app.api import (
    agent_ws,
    auth as auth_api,
    clusters,
    commands,
    issues,
    kubeconfig,
    live,
    manual,
    resources,
    rules,
    velero,
)
from app.config import get_settings
from app.core import auth
from app.db import crud
from app.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _seed_admin() -> None:
    """부트스트랩 admin 이 없으면 생성."""
    s = get_settings()
    if crud.get_user(s.admin_user) is not None:
        return
    salt = auth.make_salt()
    crud.create_user(
        s.admin_user, auth.hash_password(s.admin_password, salt), salt, role="admin"
    )
    logging.getLogger("canggu").info("부트스트랩 admin 사용자 생성: %s", s.admin_user)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.remediation.rules import RULE_CATALOG

    init_db()
    _seed_admin()
    crud.seed_rule_configs(RULE_CATALOG)
    logging.getLogger("canggu").info("hub 시작 — DB 초기화 완료 (auth=%s)",
                                     get_settings().auth_enabled)
    yield


app = FastAPI(title="canggu hub", version="0.0.1", lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST
app.include_router(auth_api.router)
# 읽기 라우터는 로그인 사용자 필요, 변경 엔드포인트는 각 핸들러에서 require_admin.
app.include_router(clusters.router)
app.include_router(resources.router, dependencies=[Depends(auth.require_user)])
app.include_router(issues.router)
app.include_router(manual.router)
app.include_router(kubeconfig.router)
app.include_router(velero.router)
app.include_router(rules.router)
app.include_router(commands.router, dependencies=[Depends(auth.require_user)])
# WebSocket
app.include_router(agent_ws.router)
app.include_router(live.router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


# 빌드된 웹 SPA 정적 서빙 (API/WS 라우트 뒤에 마운트하여 우선순위 보장).
# gateway 가 /operating/ prefix 를 strip 하므로 hub 는 루트에서 서빙한다.
if _settings.web_dist and os.path.isdir(_settings.web_dist):
    app.mount("/", StaticFiles(directory=_settings.web_dist, html=True), name="web")
    logging.getLogger("canggu").info("웹 SPA 정적 서빙: %s", _settings.web_dist)
