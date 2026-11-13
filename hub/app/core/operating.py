"""Operating 뷰: 워크로드 단위로 Pod 상태 · 리소스 사용량 · 경고 이벤트 · 이슈(제안)를 상관.

순수 함수(부수효과 없음). 입력은 hub 인메모리 스냅샷과 이슈 목록.
"""

from __future__ import annotations

import re

from app.remediation.quantity import parse_cpu, parse_mem
from app.schemas import Issue, TelemetrySnapshot, Workload

_SEV_ORDER = {"critical": 0, "warn": 1, "ok": 2}
_RS_HASH = re.compile(r"^(.+)-[a-z0-9]{5,10}$")  # ReplicaSet 해시 접미사
MAX_EVENTS = 8
_SKIP_OWNER_KINDS = {"", "Node"}  # static pod 등 — 워크로드로 보지 않음
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


def update_restart_history(
    history: dict[tuple[str, str], list], pods: list, now: float
) -> None:
    """스냅샷 간 restartCount 증가를 추적. 최초 관측은 기준선(증가 시각 None)으로만 기록.

    사라진 Pod 는 제거. 카운트가 줄면(재생성) 기준선 재설정.
    """
    seen = set()
    for p in pods:
        key = (p.namespace, p.name)
        seen.add(key)
        prev = history.get(key)
        if prev is None or p.restart_count < prev[0]:
            history[key] = [p.restart_count, None]
        elif p.restart_count > prev[0]:
            history[key] = [p.restart_count, now]
    for key in list(history):
        if key not in seen:
            del history[key]


def recent_restarts(
    history: dict[tuple[str, str], list], now: float, window: float
) -> set[tuple[str, str]]:
    """window 초 이내에 restartCount 가 증가한 Pod 집합."""
    return {k for k, (_, ts) in history.items() if ts is not None and now - ts <= window}


def parse_noise_reasons(raw: str) -> frozenset[str]:
    """콤마 구분 이벤트 reason 목록 → 집합 (설정값 파싱)."""
    return frozenset(r.strip() for r in raw.split(",") if r.strip())


def build_operating(
    snapshot: TelemetrySnapshot, issues: list[Issue], namespace: str | None,
    noise_reasons: frozenset[str] = frozenset(),
    recent_restart_pods: set[tuple[str, str]] | None = None,
) -> dict:
    """noise_reasons: 상태 판정·이벤트 수 집계에서 제외할 무해한 이벤트 reason(상세에는 표시).

    recent_restart_pods: 최근 재기동이 관측된 Pod. None 이면 누적 restartCount>0 을 문제로 본다(이력 없음).
    주어지면 누적 재기동만 있는 Running Pod 는 문제로 보지 않는다(과거 노드 재부팅 등).
    """
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

    # agent 가 스펙을 수집하지 않는 소유자(StatefulSet/DaemonSet/Job 등)는 Pod·이벤트·이슈로 보충.
    workloads = [w for w in snapshot.workloads if in_ns(w)]
    known = {(w.namespace, f"{w.kind}/{w.name}") for w in workloads}
    extra_keys = sorted(
        k for k in set(pods_by_wl) | set(events_by_wl) | set(issues_by_wl)
        if k not in known and k[1].split("/", 1)[0] not in _SKIP_OWNER_KINDS
    )
    for ns, target in extra_keys:
        kind, name = target.split("/", 1)
        pods = pods_by_wl.get((ns, target), [])
        workloads.append(Workload(
            namespace=ns, kind=kind, name=name,
            replicas_desired=len(pods),
            replicas_ready=sum(1 for p in pods if p.phase in ("Running", "Succeeded")),
            distinct_nodes=len({p.node for p in pods if p.node}),
        ))
    partial = set(extra_keys)

    rows = []
    for wl in workloads:
        key = (wl.namespace, f"{wl.kind}/{wl.name}")
        pods = pods_by_wl.get(key, [])
        all_evs = sorted(events_by_wl.get(key, []), key=lambda e: e.last_seen, reverse=True)
        evs = [e for e in all_evs if e.reason not in noise_reasons]
        noise_evs = [e for e in all_evs if e.reason in noise_reasons]
        wl_issues = issues_by_wl.get(key, [])

        def restarted(p) -> bool:
            if recent_restart_pods is None:
                return p.restart_count > 0
            return (p.namespace, p.name) in recent_restart_pods

        problem_pods = [
            {"name": p.name, "node": p.node, "phase": p.phase,
             "restarts": p.restart_count, "recent_restart": restarted(p),
             "reason": p.waiting_reason or p.last_terminated_reason
                       or ("Unschedulable" if p.unschedulable else "")}
            for p in pods
            if restarted(p) or p.waiting_reason or p.unschedulable
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
            "gitops_manager": wl.gitops_manager,
            "partial": key in partial,  # 스펙(replicas/resources) 미수집 — Pod 기준 추정
            "replicas_desired": wl.replicas_desired, "replicas_ready": wl.replicas_ready,
            "distinct_nodes": wl.distinct_nodes,
            "pods": {
                "total": len(pods),
                "restarts": sum(p.restart_count for p in pods),
                "recent_restarts": sum(1 for p in pods if restarted(p)),
                "problems": problem_pods[:MAX_PROBLEM_PODS],
                "problem_count": len(problem_pods),
            },
            "containers": containers,
            "max_usage_ratio": max_ratio,
            "events": [
                {"reason": e.reason, "message": e.message, "count": e.count,
                 "last_seen": e.last_seen, "object": f"{e.involved_kind}/{e.involved_name}",
                 "noise": e.reason in noise_reasons}
                for e in (evs + noise_evs)[:MAX_EVENTS]
            ],
            "event_count": sum(e.count for e in evs),
            "noise_event_count": sum(e.count for e in noise_evs),
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
