"""리소스 인벤토리 조회 (namespace 필터 + 웹 RBAC 스코프 강제). 소스는 hub 인메모리 스냅샷."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agentlink.manager import hub
from app.core import scope
from app.core.auth import Principal, require_user

router = APIRouter(prefix="/api/clusters", tags=["resources"])


def _snapshot(cluster_id: str):
    snap = hub.snapshots.get(cluster_id)
    if snap is None:
        raise HTTPException(404, f"cluster '{cluster_id}' 텔레메트리 없음(미접속?)")
    return snap


def _match(items, ns: str | None):
    """ns=None 이면 전체, 아니면 해당 namespace 만."""
    return items if ns is None else [i for i in items if i.namespace == ns]


@router.get("/{cluster_id}/namespaces")
async def namespaces(cluster_id: str, p: Principal = Depends(require_user)) -> list[str]:
    allowed = scope.resolve_scope(p, cluster_id)
    names = sorted(_snapshot(cluster_id).namespaces)
    return [allowed] if allowed else names


@router.get("/{cluster_id}/pods")
async def pods(
    cluster_id: str, namespace: str | None = Query(None), p: Principal = Depends(require_user)
) -> list[dict]:
    ns = scope.effective_namespace(p, cluster_id, namespace)
    return [x.model_dump() for x in _match(_snapshot(cluster_id).pods, ns)]


@router.get("/{cluster_id}/workloads")
async def workloads(
    cluster_id: str, namespace: str | None = Query(None), p: Principal = Depends(require_user)
) -> list[dict]:
    ns = scope.effective_namespace(p, cluster_id, namespace)
    return [x.model_dump() for x in _match(_snapshot(cluster_id).workloads, ns)]


@router.get("/{cluster_id}/services")
async def services(
    cluster_id: str, namespace: str | None = Query(None),
    unhealthy_only: bool = Query(False), p: Principal = Depends(require_user),
) -> list[dict]:
    ns = scope.effective_namespace(p, cluster_id, namespace)
    items = _match(_snapshot(cluster_id).services, ns)
    if unhealthy_only:
        items = [s for s in items if not s.healthy]
    return [{**s.model_dump(), "healthy": s.healthy} for s in items]


@router.get("/{cluster_id}/storage")
async def storage(
    cluster_id: str, namespace: str | None = Query(None), p: Principal = Depends(require_user)
) -> list[dict]:
    ns = scope.effective_namespace(p, cluster_id, namespace)
    return [x.model_dump() for x in _match(_snapshot(cluster_id).storage, ns)]


@router.get("/{cluster_id}/routes")
async def routes(
    cluster_id: str, namespace: str | None = Query(None), p: Principal = Depends(require_user)
) -> list[dict]:
    ns = scope.effective_namespace(p, cluster_id, namespace)
    return [x.model_dump() for x in _match(_snapshot(cluster_id).routes, ns)]
