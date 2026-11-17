"""canggu hub 진입점."""

from __future__ import annotations

import asyncio
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
    """부트스트랩 admin 생성. CANGGU_ADMIN_RESET=true 면 기존 admin 비밀번호를 재동기화(복구)."""
    s = get_settings()
    log = logging.getLogger("canggu")
    existing = crud.get_user(s.admin_user)
    if existing is None:
        salt = auth.make_salt()
        crud.create_user(
            s.admin_user, auth.hash_password(s.admin_password, salt), salt, role="admin"
        )
        log.info("부트스트랩 admin 사용자 생성: %s", s.admin_user)
        return
    if s.admin_reset:
        salt = auth.make_salt()
        crud.update_user(
            s.admin_user, role="admin",
            pw_hash=auth.hash_password(s.admin_password, salt), salt=salt,
        )
        log.warning(
            "CANGGU_ADMIN_RESET=true → admin '%s' 비밀번호를 Secret 값으로 재설정함. "
            "복구 후 CANGGU_ADMIN_RESET 을 false 로 되돌리세요.", s.admin_user,
        )


async def expire_active_once() -> list[dict]:
    """ACTIVE 기한 초과 범위를 OBSERVE 로 되돌리고 live 이벤트로 알린다."""
    from starlette.concurrency import run_in_threadpool

    from app.agentlink.manager import hub

    expired = await run_in_threadpool(
        crud.expire_active_modes, get_settings().mode_active_max_seconds
    )
    for e in expired:
        ns = e["namespace"]
        logging.getLogger("canggu").warning(
            "ACTIVE 기한 초과 → OBSERVE 자동 복귀: %s%s", e["cluster_id"], f"/{ns}" if ns else ""
        )
        await hub.broadcast_event({
            "kind": "mode_changed", "cluster_id": e["cluster_id"], "mode": "OBSERVE",
            "scope": "namespace" if ns else "cluster", **({"namespace": ns} if ns else {}),
            "by": "system:auto-revert", "reason": "active_expired",
        })
    return expired


async def _active_expiry_loop() -> None:
    log = logging.getLogger("canggu")
    while True:
        try:
            await expire_active_once()
        except Exception:  # noqa: BLE001 — 루프는 계속
            log.exception("ACTIVE 자동 복귀 점검 실패")
        await asyncio.sleep(get_settings().mode_expiry_check_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.remediation.rules import RULE_CATALOG

    init_db()
    _seed_admin()
    crud.seed_rule_configs(RULE_CATALOG)
    logging.getLogger("canggu").info("hub 시작 — DB 초기화 완료 (auth=%s)",
                                     get_settings().auth_enabled)
    expiry_task = asyncio.create_task(_active_expiry_loop())
    try:
        yield
    finally:
        expiry_task.cancel()


app = FastAPI(title="canggu hub", version=get_settings().hub_version, lifespan=lifespan)

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
    s = get_settings()
    return {"status": "ok", "version": s.hub_version, "revision": s.hub_revision}


# 빌드된 웹 SPA 정적 서빙 (API/WS 라우트 뒤에 마운트하여 우선순위 보장).
# gateway 가 /operating/ prefix 를 strip 하므로 hub 는 루트에서 서빙한다.
if _settings.web_dist and os.path.isdir(_settings.web_dist):
    app.mount("/", StaticFiles(directory=_settings.web_dist, html=True), name="web")
    logging.getLogger("canggu").info("웹 SPA 정적 서빙: %s", _settings.web_dist)
