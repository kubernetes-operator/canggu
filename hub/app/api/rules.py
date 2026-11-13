"""자동조정 규칙 런타임 설정(활성/자동적용). 조회는 로그인 사용자, 변경은 admin."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core import auth
from app.db import crud
from app.remediation.rules import RULE_CATALOG

router = APIRouter(prefix="/api/rules", tags=["rules"])


class RulePatch(BaseModel):
    enabled: bool | None = None
    auto_apply: Literal["default", "on", "off"] | None = None


@router.get("")
async def list_rules(_: auth.Principal = Depends(auth.require_user)) -> list[dict]:
    cfg = await run_in_threadpool(crud.get_rule_configs)
    out = []
    for r in RULE_CATALOG:
        c = cfg.get(r["id"], {"enabled": True, "auto_apply": "default"})
        out.append({**r, "enabled": c["enabled"], "auto_apply": c["auto_apply"]})
    return out


@router.patch("/{rule_id}")
async def patch_rule(
    rule_id: str, body: RulePatch, _: auth.Principal = Depends(auth.require_admin)
) -> dict:
    if rule_id not in {r["id"] for r in RULE_CATALOG}:
        raise HTTPException(404, "알 수 없는 규칙")
    await run_in_threadpool(crud.set_rule_config, rule_id, body.enabled, body.auto_apply)
    return {"ok": True, "rule_id": rule_id}
