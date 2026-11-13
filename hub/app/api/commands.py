"""자동조정 명령 감사 원장 조회."""

from __future__ import annotations

from fastapi import APIRouter, Query
from starlette.concurrency import run_in_threadpool

from app.db import crud

router = APIRouter(prefix="/api/commands", tags=["audit"])


@router.get("")
async def list_commands(
    cluster_id: str | None = Query(None), limit: int = Query(100, le=500)
) -> list[dict]:
    rows = await run_in_threadpool(crud.recent_commands, cluster_id, limit)
    return [
        {
            "command_id": r.command_id,
            "cluster_id": r.cluster_id,
            "namespace": r.namespace,
            "target": f"{r.target_kind}/{r.target_name}",
            "rule_id": r.rule_id,
            "action_type": r.action_type,
            "risk_tier": r.risk_tier,
            "dry_run": r.dry_run,
            "mode_at_issue": r.mode_at_issue,
            "issued_by": r.issued_by,
            "phase": r.phase,
            "resource_version_before": r.resource_version_before,
            "resource_version_after": r.resource_version_after,
            "error": r.error,
            "issued_at": r.issued_at.isoformat() if r.issued_at else None,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
        for r in rows
    ]
