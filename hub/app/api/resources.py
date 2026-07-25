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


@router.get("/{cluster_id}/events")
async def events(
    cluster_id: str, namespace: str | None = Query(None),
    reason: str | None = Query(None), p: Principal = Depends(require_user),
) -> list[dict]:
    ns = scope.effective_namespace(p, cluster_id, namespace)
    items = _match(_snapshot(cluster_id).events, ns)
    if reason:
        items = [e for e in items if e.reason == reason]
    return [e.model_dump() for e in items]


@router.get("/{cluster_id}/summary")
async def summary(cluster_id: str, p: Principal = Depends(require_user)) -> dict:
    """클러스터 헬스 요약(스코프 적용). 심각도별 이슈·구성요소 이상·헬스 스코어."""
    from app.agentlink.manager import hub as _hub

    snap = _snapshot(cluster_id)
    ns = scope.resolve_scope(p, cluster_id)  # None=전체

    def scoped(items):
        return items if ns is None else [i for i in items if i.namespace == ns]

    pods = scoped(snap.pods)
    svcs = scoped(snap.services)
    storage = scoped(snap.storage)
    workloads = scoped(snap.workloads)
    warn_events = len(scoped(snap.events))
    issues = scoped(_hub.issues.get(cluster_id, []))

    by_sev = {"critical": 0, "warn": 0, "info": 0}
    by_rule: dict[str, int] = {}
    for i in issues:
        by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
        by_rule[i.rule_id] = by_rule.get(i.rule_id, 0) + 1

    restarting = sum(1 for x in pods if x.restart_count > 0)
    unhealthy_svc = sum(1 for s in svcs if not s.healthy)
    unbound_pvc = sum(1 for s in storage if s.phase != "Bound")
    single_node = sum(
        1 for w in workloads if w.replicas_desired >= 2 and w.distinct_nodes < 2
    )

    score = 100 - (by_sev["critical"] * 3 + by_sev["warn"] * 1
                   + unhealthy_svc * 2 + unbound_pvc * 2 + single_node)
    score = max(0, min(100, score))

    return {
        "cluster_id": cluster_id,
        "scope_namespace": ns,
        "health_score": score,
        "totals": {"namespaces": 1 if ns else len(snap.namespaces),
                   "pods": len(pods), "services": len(svcs), "workloads": len(workloads)},
        "issues_total": len(issues),
        "issues_by_severity": by_sev,
        "issues_by_rule": by_rule,
        "restarting_pods": restarting,
        "unhealthy_services": unhealthy_svc,
        "unbound_pvcs": unbound_pvc,
        "workloads_single_node": single_node,
        "warning_events": warn_events,
    }
