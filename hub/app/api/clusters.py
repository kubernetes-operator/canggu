"""클러스터 레지스트리 + 모드 토글 API.

주의: 웹 RBAC(scope/level) 강제는 Phase 3. Phase 0 엔드포인트는 인가 미적용(로컬 개발용).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from fastapi import HTTPException

from app.agentlink.manager import hub
from app.core import auth, scope
from app.db import crud
from app.schemas import Mode

router = APIRouter(prefix="/api/clusters", tags=["clusters"])


class ModeBody(BaseModel):
    mode: Mode


class FreezeBody(BaseModel):
    frozen: bool


@router.get("")
async def list_clusters(p: auth.Principal = Depends(auth.require_user)) -> list[dict]:
    clusters = await run_in_threadpool(crud.list_clusters)
    out = []
    for c in clusters:
        if not scope.cluster_allowed(p, c.id):
            continue
        allowed_ns = scope.resolve_scope(p, c.id)  # None=전체
        snap = hub.snapshots.get(c.id)
        pods = snap.pods if snap else []
        svcs = snap.services if snap else []
        issues = hub.issues.get(c.id, [])
        nslist = snap.namespaces if snap else []
        if allowed_ns:  # 네임스페이스 스코프 사용자 → 해당 NS 로 카운트 한정
            pods = [x for x in pods if x.namespace == allowed_ns]
            svcs = [x for x in svcs if x.namespace == allowed_ns]
            issues = [x for x in issues if x.namespace == allowed_ns]
            nslist = [allowed_ns] if allowed_ns in nslist else []
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "cluster_mode": c.cluster_mode,
                "remediation_frozen": c.remediation_frozen,
                "connected": c.id in hub.agents,
                "agent_version": c.agent_version,
                "metrics_available": c.metrics_available,
                "last_seen": c.last_seen.isoformat() if c.last_seen else None,
                "scope": {"type": p.scope_type, "namespace": allowed_ns},
                "namespaces": len(nslist),
                "pods": len(pods),
                "services": len(svcs),
                "unhealthy_services": sum(1 for s in svcs if not s.healthy),
                "restarting_pods": sum(1 for x in pods if x.restart_count > 0),
                "open_issues": len(issues),
            }
        )
    return out


@router.get("/{cluster_id}/namespace-modes")
async def namespace_modes(
    cluster_id: str, p: auth.Principal = Depends(auth.require_user)
) -> dict:
    """네임스페이스별 모드 오버라이드 맵. 없는 NS 는 클러스터 모드를 상속."""
    allowed = scope.resolve_scope(p, cluster_id)
    modes = await run_in_threadpool(crud.ns_modes, cluster_id)
    return {allowed: modes.get(allowed)} if allowed else modes


@router.post("/{cluster_id}/mode")
async def set_cluster_mode(
    cluster_id: str, body: ModeBody, p: auth.Principal = Depends(auth.require_admin)
) -> dict:
    if p.scope_type != "cluster":
        raise HTTPException(403, "클러스터 모드 변경은 클러스터 범위 admin 만 가능합니다")
    scope.resolve_scope(p, cluster_id)
    await run_in_threadpool(crud.set_cluster_mode, cluster_id, body.mode, p.username)
    await hub.broadcast_event(
        {"kind": "mode_changed", "cluster_id": cluster_id, "mode": body.mode, "scope": "cluster"}
    )
    return {"ok": True, "cluster_id": cluster_id, "mode": body.mode}


@router.post("/{cluster_id}/namespaces/{namespace}/mode")
async def set_ns_mode(
    cluster_id: str, namespace: str, body: ModeBody,
    p: auth.Principal = Depends(auth.require_admin),
) -> dict:
    scope.effective_namespace(p, cluster_id, namespace)  # 스코프 밖이면 403
    await run_in_threadpool(
        crud.set_namespace_mode, cluster_id, namespace, body.mode, p.username
    )
    await hub.broadcast_event(
        {
            "kind": "mode_changed",
            "cluster_id": cluster_id,
            "namespace": namespace,
            "mode": body.mode,
            "scope": "namespace",
        }
    )
    return {"ok": True, "cluster_id": cluster_id, "namespace": namespace, "mode": body.mode}


@router.post("/{cluster_id}/freeze")
async def set_freeze(
    cluster_id: str, body: FreezeBody, p: auth.Principal = Depends(auth.require_admin)
) -> dict:
    if p.scope_type != "cluster":
        raise HTTPException(403, "freeze 는 클러스터 범위 admin 만 가능합니다")
    scope.resolve_scope(p, cluster_id)
    await run_in_threadpool(crud.set_frozen, cluster_id, body.frozen)
    await hub.broadcast_event(
        {"kind": "freeze_changed", "cluster_id": cluster_id, "frozen": body.frozen}
    )
    return {"ok": True, "cluster_id": cluster_id, "frozen": body.frozen}
