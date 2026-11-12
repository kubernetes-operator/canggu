"""Operating 뷰: 워크로드 단위로 Pod 상태 · 리소스 사용량 · 경고 이벤트 · 이슈(제안)를 상관.

순수 함수(부수효과 없음). 입력은 hub 인메모리 스냅샷과 이슈 목록.
"""

from __future__ import annotations

import re

from app.remediation.quantity import parse_cpu, parse_mem
from app.schemas import Issue, TelemetrySnapshot

_SEV_ORDER = {"critical": 0, "warn": 1, "ok": 2}
_RS_HASH = re.compile(r"^(.+)-[a-z0-9]{5,10}$")  # ReplicaSet 해시 접미사
MAX_EVENTS = 8
MAX_PROBLEM_PODS = 8


def _ratio(usage: str, base: str, parse) -> float | None:
    try:
        u, b = parse(usage), parse(base)
    except Exception:  # noqa: BLE001 — 형식 이상치는 비율 미표시
        return None
    if not u or not b:
        return None
    return round(u / b, 2)


def _event_target(e, pod_owner: dict[tuple[str, str], str]) -> str:
    """이벤트 대상 → 워크로드 "Kind/Name". Pod 는 소유자로, ReplicaSet 은 Deployment 로 환원."""
    if e.involved_kind == "Pod":
        return pod_owner.get((e.namespace, e.involved_name), "")
    if e.involved_kind == "ReplicaSet":
        m = _RS_HASH.match(e.involved_name)
        return f"Deployment/{m.group(1)}" if m else ""
    return f"{e.involved_kind}/{e.involved_name}"


def build_operating(
    snapshot: TelemetrySnapshot, issues: list[Issue], namespace: str | None
) -> dict:
    def in_ns(x) -> bool:
        return namespace is None or x.namespace == namespace

    pods_by_wl: dict[tuple[str, str], list] = {}
    pod_owner: dict[tuple[str, str], str] = {}
    for p in snapshot.pods:
        if p.owner_name and in_ns(p):
            key = f"{p.owner_kind}/{p.owner_name}"
            pods_by_wl.setdefault((p.namespace, key), []).append(p)
            pod_owner[(p.namespace, p.name)] = key

    events_by_wl: dict[tuple[str, str], list] = {}
    for e in snapshot.events:
        if not in_ns(e):
            continue
        t = _event_target(e, pod_owner)
        if t:
            events_by_wl.setdefault((e.namespace, t), []).append(e)

    issues_by_wl: dict[tuple[str, str], list[Issue]] = {}
    for i in issues:
        if in_ns(i) and i.target:
            issues_by_wl.setdefault((i.namespace, i.target), []).append(i)

    rows = []
    for wl in snapshot.workloads:
        if not in_ns(wl):
            continue
        key = (wl.namespace, f"{wl.kind}/{wl.name}")
        pods = pods_by_wl.get(key, [])
        evs = sorted(events_by_wl.get(key, []), key=lambda e: e.last_seen, reverse=True)
        wl_issues = issues_by_wl.get(key, [])

        problem_pods = [
            {"name": p.name, "node": p.node, "phase": p.phase,
             "restarts": p.restart_count,
             "reason": p.waiting_reason or p.last_terminated_reason
                       or ("Unschedulable" if p.unschedulable else "")}
            for p in pods
            if p.restart_count > 0 or p.waiting_reason or p.unschedulable
            or p.phase not in ("Running", "Succeeded")
        ]

        containers = []
        max_ratio = None
        for c in wl.containers:
            cpu_r = _ratio(c.usage_p95.cpu, c.requests.cpu, parse_cpu)
            mem_r = _ratio(c.usage_p95.memory, c.requests.memory, parse_mem)
            for r in (cpu_r, mem_r):
                if r is not None and (max_ratio is None or r > max_ratio):
                    max_ratio = r
            containers.append({
                "name": c.name,
                "requests": c.requests.model_dump(), "limits": c.limits.model_dump(),
                "usage_p95": c.usage_p95.model_dump(),
                "cpu_ratio": cpu_r, "mem_ratio": mem_r,
            })

        sevs = {i.severity for i in wl_issues}
        degraded = wl.replicas_desired > 0 and wl.replicas_ready < wl.replicas_desired
        if "critical" in sevs or (wl.replicas_desired > 0 and wl.replicas_ready == 0):
            status = "critical"
        elif wl_issues or evs or degraded or problem_pods:
            status = "warn"
        else:
            status = "ok"

        rows.append({
            "namespace": wl.namespace, "kind": wl.kind, "name": wl.name,
            "status": status,
            "replicas_desired": wl.replicas_desired, "replicas_ready": wl.replicas_ready,
            "distinct_nodes": wl.distinct_nodes,
            "pods": {
                "total": len(pods),
                "restarts": sum(p.restart_count for p in pods),
                "problems": problem_pods[:MAX_PROBLEM_PODS],
                "problem_count": len(problem_pods),
            },
            "containers": containers,
            "max_usage_ratio": max_ratio,
            "events": [
                {"reason": e.reason, "message": e.message, "count": e.count,
                 "last_seen": e.last_seen, "object": f"{e.involved_kind}/{e.involved_name}"}
                for e in evs[:MAX_EVENTS]
            ],
            "event_count": sum(e.count for e in evs),
            "issues": [
                {"fingerprint": i.fingerprint, "rule_id": i.rule_id, "severity": i.severity,
                 "title": i.title, "detail": i.detail, "disposition": i.disposition,
                 "has_action": i.suggested_action is not None,
                 "risk_tier": i.suggested_action.risk_tier if i.suggested_action else None}
                for i in wl_issues
            ],
        })

    rows.sort(key=lambda r: (_SEV_ORDER[r["status"]], -len(r["issues"]),
                             -r["event_count"], r["namespace"], r["name"]))
    counts = {"ok": 0, "warn": 0, "critical": 0}
    for r in rows:
        counts[r["status"]] += 1
    return {"namespace": namespace, "counts": counts, "workloads": rows}
