"""Velero 백업/복구(기능 #10). 조회는 로그인 사용자, 실행은 admin.

백업/복구 CR 생성은 agent 가 수행. 복구는 파괴적일 수 있어 admin 전용 + 감사.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.agentlink.manager import hub
from app.core import auth, scope
from app.db import crud
from app.schemas import Command

router = APIRouter(prefix="/api/clusters", tags=["velero"])


class BackupBody(BaseModel):
    namespace: str


class RestoreBody(BaseModel):
    backup_name: str


def _snapshot(cluster_id: str):
    snap = hub.snapshots.get(cluster_id)
    if snap is None:
        raise HTTPException(404, "텔레메트리 없음")
    return snap


@router.get("/{cluster_id}/velero")
async def velero_state(cluster_id: str, p: auth.Principal = Depends(auth.require_user)) -> dict:
    snap = _snapshot(cluster_id)
    allowed = scope.resolve_scope(p, cluster_id)
    backups = snap.velero_backups
    restores = snap.velero_restores
    if allowed:  # 네임스페이스 스코프 → 해당 NS 포함 백업만
        backups = [b for b in backups if allowed in b.included_namespaces]
        restores = []  # 복구는 클러스터 범위 정보 — NS 스코프엔 숨김
    return {
        "installed": snap.velero_installed,
        "backups": [b.model_dump() for b in backups],
        "restores": [r.model_dump() for r in restores],
    }


async def _dispatch(cluster_id: str, cmd: Command, action_label: str, by: str) -> str:
    if cluster_id not in hub.agents:
        raise HTTPException(409, "agent 미접속")
    await run_in_threadpool(crud.record_command, cmd, action_label, "high", "MANUAL")
    try:
        result = await hub.request_command(cmd, timeout=20.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(504, f"실행 실패: {e}") from e
    if result.phase != "APPLIED":
        raise HTTPException(502, f"실행 실패: {result.error or result.phase}")
    return result.payload


@router.post("/{cluster_id}/velero/backup")
async def create_backup(
    cluster_id: str, body: BackupBody, p: auth.Principal = Depends(auth.require_admin)
) -> dict:
    scope.effective_namespace(p, cluster_id, body.namespace)  # 스코프 밖이면 403
    name = f"{body.namespace}-{uuid.uuid4().hex[:8]}"
    seq = hub._seq.get(cluster_id, 0) + 1
    hub._seq[cluster_id] = seq
    cmd = Command(
        command_id=str(uuid.uuid4()), command_seq=seq, cluster_id=cluster_id,
        namespace=body.namespace, target_kind="Backup", target_name=name,
        type="velero-backup", patch={"name": name}, issued_by=p.username,
    )
    created = await _dispatch(cluster_id, cmd, "velero-backup", p.username)
    await hub.broadcast_event({"kind": "velero_backup", "cluster_id": cluster_id,
                               "namespace": body.namespace, "name": created, "by": p.username})
    return {"ok": True, "backup": created, "namespace": body.namespace}


@router.post("/{cluster_id}/velero/restore")
async def create_restore(
    cluster_id: str, body: RestoreBody, p: auth.Principal = Depends(auth.require_admin)
) -> dict:
    if p.scope_type != "cluster":
        raise HTTPException(403, "복구는 클러스터 범위 admin 만 가능합니다")
    scope.resolve_scope(p, cluster_id)
    name = f"restore-{body.backup_name}-{uuid.uuid4().hex[:6]}"[:63]
    seq = hub._seq.get(cluster_id, 0) + 1
    hub._seq[cluster_id] = seq
    cmd = Command(
        command_id=str(uuid.uuid4()), command_seq=seq, cluster_id=cluster_id,
        namespace="velero", target_kind="Restore", target_name=name,
        type="velero-restore", patch={"name": name, "backup_name": body.backup_name},
        issued_by=p.username,
    )
    created = await _dispatch(cluster_id, cmd, "velero-restore", p.username)
    await hub.broadcast_event({"kind": "velero_restore", "cluster_id": cluster_id,
                               "backup": body.backup_name, "name": created, "by": p.username})
    return {"ok": True, "restore": created, "backup_name": body.backup_name}
