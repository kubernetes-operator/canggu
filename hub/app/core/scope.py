"""웹 RBAC 스코프 강제.

Principal.scope_type:
  - "cluster": scope_ref 가 "" 이면 전체 클러스터, 아니면 특정 cluster_id 만.
  - "namespace": scope_ref = "<cluster_id>/<namespace>" — 해당 클러스터의 그 네임스페이스만.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.auth import Principal


def resolve_scope(principal: Principal, cluster_id: str) -> str | None:
    """이 클러스터에서 principal 이 제한되는 네임스페이스 반환(None=전체). 클러스터 불허 시 403."""
    if principal.scope_type == "namespace":
        c, _, ns = principal.scope_ref.partition("/")
        if c != cluster_id:
            raise HTTPException(403, "이 클러스터에 대한 권한이 없습니다")
        return ns or None
    # cluster scope
    if principal.scope_ref and principal.scope_ref != cluster_id:
        raise HTTPException(403, "이 클러스터에 대한 권한이 없습니다")
    return None


def effective_namespace(principal: Principal, cluster_id: str, requested: str | None) -> str | None:
    """요청 namespace 를 스코프로 보정. 제한 사용자가 타 NS 를 요청하면 403.

    반환 None = 전체(스코프 허용 범위), 그 외 = 강제된 특정 네임스페이스.
    """
    allowed = resolve_scope(principal, cluster_id)
    if allowed is None:
        return requested if requested not in ("", "all") else None
    if requested not in (None, "", "all", allowed):
        raise HTTPException(403, f"네임스페이스 '{requested}' 접근 권한이 없습니다")
    return allowed


def event_visible(principal: Principal, event: dict) -> bool:
    """라이브 피드 이벤트를 principal 스코프로 필터. cluster/namespace 불일치 시 숨김."""
    cid = event.get("cluster_id")
    if cid and not cluster_allowed(principal, cid):
        return False
    if principal.scope_type == "namespace":
        _, _, ns = principal.scope_ref.partition("/")
        ev_ns = event.get("namespace")
        if ev_ns and ev_ns != ns:
            return False
    return True


def cluster_allowed(principal: Principal, cluster_id: str) -> bool:
    try:
        resolve_scope(principal, cluster_id)
        return True
    except HTTPException:
        return False
