"""수동 리소스 편집 — 운영자가 직접 워크로드 patch 를 적용.

명시적 사용자 액션이므로 OBSERVE 모드에서도 허용(단, frozen 이면 차단).
Phase 3 에서 웹 RBAC(admin) 인가를 강제한다. Phase 0~2 는 로컬/인가 미적용.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.agentlink.manager import hub
from app.core import auth, scope
from app.db import crud
from app.schemas import Command

router = APIRouter(prefix="/api/clusters", tags=["manual"])

_ALLOWED_KINDS = {"Deployment", "StatefulSet"}


class PatchBody(BaseModel):
    patch: dict = Field(..., description="strategic-merge patch (예: resources 변경)")
    patch_type: str = "strategic"
    dry_run: bool = False


@router.post("/{cluster_id}/workloads/{namespace}/{kind}/{name}/patch")
async def patch_workload(
    cluster_id: str, namespace: str, kind: str, name: str, body: PatchBody,
    p: auth.Principal = Depends(auth.require_admin),
) -> dict:
    if kind not in _ALLOWED_KINDS:
        raise HTTPException(400, f"지원하지 않는 kind: {kind}")
    if not body.patch:
        raise HTTPException(400, "빈 patch")
    scope.effective_namespace(p, cluster_id, namespace)  # 스코프 밖이면 403

    _, frozen = await run_in_threadpool(crud.cluster_mode_and_frozen, cluster_id)
    if frozen:
        raise HTTPException(409, "remediation 이 전역 freeze 상태입니다")
    if cluster_id not in hub.agents:
        raise HTTPException(409, "agent 미접속")

    seq = hub._seq.get(cluster_id, 0) + 1
    hub._seq[cluster_id] = seq
    cmd = Command(
        command_id=str(uuid.uuid4()),
        command_seq=seq,
        cluster_id=cluster_id,
        namespace=namespace,
        target_kind=kind,
        target_name=name,
        type="manual-edit",
        patch=body.patch,
        patch_type=body.patch_type,
        dry_run=body.dry_run,
        issued_by=p.username,
    )
    await run_in_threadpool(crud.record_command, cmd, "manual", "med", "MANUAL")
    await hub.broadcast_event(
        {
            "kind": "command_issued",
            "cluster_id": cluster_id,
            "namespace": namespace,
            "target": f"{kind}/{name}",
            "action": "manual-edit",
            "command_id": cmd.command_id,
            "manual": True,
        }
    )
    ok = await hub.send_command(cmd)
    return {"ok": ok, "command_id": cmd.command_id, "dry_run": body.dry_run}
