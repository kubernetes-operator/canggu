"""이슈 목록 + 제안 조치 수동 적용(웹-admin 액션).

관찰 전용 모드에서도 수동 적용은 명시적 사용자 액션이므로 허용한다(단, frozen 이면 차단).
Phase 3 에서 웹 RBAC(admin) 인가를 이 엔드포인트에 강제한다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.agentlink.manager import hub
from app.core import auth, scope
from app.db import crud
from app.schemas import Command

router = APIRouter(prefix="/api/clusters", tags=["issues"])


class ApplyBody(BaseModel):
    dry_run: bool = False


@router.get("/{cluster_id}/issues")
async def list_issues(
    cluster_id: str, namespace: str | None = Query(None),
    p: auth.Principal = Depends(auth.require_user),
) -> list[dict]:
    ns = scope.effective_namespace(p, cluster_id, namespace)
    issues = hub.issues.get(cluster_id, [])
    if ns is not None:
        issues = [i for i in issues if i.namespace == ns]
    return [i.model_dump() for i in issues]


@router.post("/{cluster_id}/issues/{fingerprint}/apply")
async def apply_issue(
    cluster_id: str, fingerprint: str, body: ApplyBody,
    p: auth.Principal = Depends(auth.require_admin),
) -> dict:
    issue = next(
        (i for i in hub.issues.get(cluster_id, []) if i.fingerprint == fingerprint), None
    )
    if issue is None:
        raise HTTPException(404, "이슈를 찾을 수 없음")
    if issue.suggested_action is None:
        raise HTTPException(400, "제안 조치가 없는 이슈")
    scope.effective_namespace(p, cluster_id, issue.namespace)  # 스코프 밖이면 403

    _, frozen = await run_in_threadpool(crud.cluster_mode_and_frozen, cluster_id)
    if frozen:
        raise HTTPException(409, "remediation 이 전역 freeze 상태입니다")

    if cluster_id not in hub.agents:
        raise HTTPException(409, "agent 미접속")

    sa = issue.suggested_action
    seq = hub._seq.get(cluster_id, 0) + 1
    hub._seq[cluster_id] = seq
    cmd = Command(
        command_id=str(uuid.uuid4()),
        command_seq=seq,
        cluster_id=cluster_id,
        namespace=sa.namespace,
        target_kind=sa.target_kind,
        target_name=sa.target_name,
        type=sa.action_type,
        patch=sa.patch,
        patch_type=sa.patch_type,
        dry_run=body.dry_run,
        issued_by=p.username,
    )
    await run_in_threadpool(
        crud.record_command, cmd, issue.rule_id, sa.risk_tier, "MANUAL"
    )
    await hub.broadcast_event(
        {
            "kind": "command_issued",
            "cluster_id": cluster_id,
            "namespace": cmd.namespace,
            "target": f"{cmd.target_kind}/{cmd.target_name}",
            "action": cmd.type,
            "command_id": cmd.command_id,
            "manual": True,
        }
    )
    ok = await hub.send_command(cmd)
    issue.disposition = "applied"
    return {"ok": ok, "command_id": cmd.command_id, "dry_run": body.dry_run}
