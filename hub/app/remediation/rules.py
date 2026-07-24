"""자동조정 규칙 정의.

각 detector 는 텔레메트리 조각을 받아 Issue|None 을 반환한다.
Phase 0 는 규칙 #3(resource-rightsize)만 구현. 나머지는 로드맵상 스텁으로 표시.
전체 규칙 명세는 docs/remediation-rules.md.
"""

from __future__ import annotations

import hashlib

from app.remediation.quantity import (
    format_cpu,
    format_mem,
    parse_cpu,
    parse_mem,
)
from app.schemas import Issue, Pod, SuggestedAction, Workload

HEADROOM = 1.2       # 우측정렬 시 p95 위에 얹는 여유
OOM_BUMP = 1.5       # OOM 시 메모리 limit 증가 배수
CPU_LIMIT_BUMP = 1.5  # CPU limit 근접 시 증가 배수
CPU_LIMIT_NEAR = 0.9  # usage 가 limit 의 이 비율 이상이면 throttling 의심


def _fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def detect_rightsize(
    cluster_id: str, wl: Workload, factor: float
) -> Issue | None:
    """규칙 #3: usage_p95 가 requests 를 크게 초과하거나 requests 미설정 → 우측정렬 제안.

    metrics(usage_p95) 가 없으면 안전상 조치하지 않는다(fail-safe). limit 미설정만으로는
    성능부족으로 단정하지 않는다.
    """
    if wl.kind not in ("Deployment", "StatefulSet"):
        return None

    changed: dict[str, dict[str, str]] = {}
    evidence: dict[str, dict] = {}

    for c in wl.containers:
        up_cpu = parse_cpu(c.usage_p95.cpu)
        up_mem = parse_mem(c.usage_p95.memory)
        if up_cpu is None and up_mem is None:
            continue  # 메트릭 없음 → 스킵 (fail-safe)

        req_cpu = parse_cpu(c.requests.cpu)
        req_mem = parse_mem(c.requests.memory)
        new_req: dict[str, str] = {}
        ev: dict[str, str] = {}

        # CPU: requests 미설정이거나 p95 가 requests*factor 초과
        if up_cpu is not None and (req_cpu is None or up_cpu > req_cpu * factor):
            new_req["cpu"] = format_cpu(up_cpu * HEADROOM)
            ev["cpu"] = f"req={c.requests.cpu or 'unset'} p95={c.usage_p95.cpu} -> {new_req['cpu']}"

        # Memory
        if up_mem is not None and (req_mem is None or up_mem > req_mem * factor):
            new_req["memory"] = format_mem(int(up_mem * HEADROOM))
            ev["memory"] = (
                f"req={c.requests.memory or 'unset'} p95={c.usage_p95.memory} -> {new_req['memory']}"
            )

        if new_req:
            changed[c.name] = new_req
            evidence[c.name] = ev

    if not changed:
        return None

    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": cname, "resources": {"requests": req}}
                        for cname, req in changed.items()
                    ]
                }
            }
        }
    }

    return Issue(
        fingerprint=_fingerprint(cluster_id, wl.namespace, wl.kind, wl.name, "rightsize"),
        rule_id="resource-rightsize",
        cluster_id=cluster_id,
        namespace=wl.namespace,
        severity="warn",
        title=f"{wl.kind}/{wl.name}: 리소스 requests 가 실사용 대비 부족",
        detail="관측된 p95 사용량이 설정된 requests 를 초과합니다. requests 우측정렬을 제안합니다.",
        evidence=evidence,
        suggested_action=SuggestedAction(
            action_type="rightsize",
            target_kind=wl.kind,
            target_name=wl.name,
            namespace=wl.namespace,
            patch=patch,
            patch_type="strategic",
            risk_tier="low",
            auto_apply=True,
        ),
    )


def _mem_patch(wl: Workload, changed: dict[str, str]) -> dict:
    return {
        "spec": {"template": {"spec": {"containers": [
            {"name": c, "resources": {"limits": {"memory": m}}} for c, m in changed.items()
        ]}}}
    }


def detect_oom_killed(cluster_id: str, wl: Workload, pods: list[Pod]) -> Issue | None:
    """규칙 #1: 소유 pod 컨테이너가 OOMKilled → 메모리 limit 단계 증가(+50%)."""
    if wl.kind not in ("Deployment", "StatefulSet"):
        return None
    oomed = [p for p in pods if p.last_terminated_reason == "OOMKilled"]
    if not oomed:
        return None

    changed: dict[str, str] = {}
    for c in wl.containers:
        lim = parse_mem(c.limits.memory)
        if lim is not None:
            changed[c.name] = format_mem(int(lim * OOM_BUMP))
        else:
            up = parse_mem(c.usage_p95.memory)
            if up is not None:
                changed[c.name] = format_mem(int(up * 2))
    if not changed:
        return None

    return Issue(
        fingerprint=_fingerprint(cluster_id, wl.namespace, wl.kind, wl.name, "oom"),
        rule_id="oom-killed",
        cluster_id=cluster_id,
        namespace=wl.namespace,
        severity="critical",
        title=f"{wl.kind}/{wl.name}: OOMKilled 발생 — 메모리 부족",
        detail=f"{len(oomed)}개 pod 가 메모리 한계로 종료되었습니다. 메모리 limit 상향을 제안합니다.",
        evidence={"oom_pods": [p.name for p in oomed[:5]], "new_limits": changed},
        suggested_action=SuggestedAction(
            action_type="bump-memory-limit", target_kind=wl.kind, target_name=wl.name,
            namespace=wl.namespace, patch=_mem_patch(wl, changed), patch_type="strategic",
            risk_tier="med", auto_apply=True,
        ),
    )


def detect_cpu_at_limit(cluster_id: str, wl: Workload) -> Issue | None:
    """규칙 #2(프록시): usage 가 CPU limit 에 근접 → throttling 의심, limit 상향.

    정확한 throttling 은 Prometheus(cfs_throttled)가 필요(향후). 여기서는 metrics-server
    usage 대비 limit 근접으로 근사한다.
    """
    if wl.kind not in ("Deployment", "StatefulSet"):
        return None
    changed: dict[str, str] = {}
    evidence: dict[str, str] = {}
    for c in wl.containers:
        lim = parse_cpu(c.limits.cpu)
        use = parse_cpu(c.usage_p95.cpu)
        if lim is not None and use is not None and use >= lim * CPU_LIMIT_NEAR:
            changed[c.name] = format_cpu(lim * CPU_LIMIT_BUMP)
            evidence[c.name] = f"usage={c.usage_p95.cpu} limit={c.limits.cpu} -> {changed[c.name]}"
    if not changed:
        return None

    patch = {"spec": {"template": {"spec": {"containers": [
        {"name": c, "resources": {"limits": {"cpu": v}}} for c, v in changed.items()
    ]}}}}
    return Issue(
        fingerprint=_fingerprint(cluster_id, wl.namespace, wl.kind, wl.name, "cpu-limit"),
        rule_id="cpu-throttling",
        cluster_id=cluster_id,
        namespace=wl.namespace,
        severity="warn",
        title=f"{wl.kind}/{wl.name}: CPU 사용량이 limit 에 근접(throttling 의심)",
        detail="관측 사용량이 CPU limit 에 근접합니다. limit 상향을 제안합니다(프록시 탐지).",
        evidence=evidence,
        suggested_action=SuggestedAction(
            action_type="bump-cpu-limit", target_kind=wl.kind, target_name=wl.name,
            namespace=wl.namespace, patch=patch, patch_type="strategic",
            risk_tier="med", auto_apply=True,
        ),
    )


def detect_crashloop(cluster_id: str, wl: Workload, pods: list[Pod]) -> Issue | None:
    """규칙 #4: CrashLoopBackOff → 진단만(무조건 재시작 금지, high-risk). 자동 조치 없음."""
    looping = [p for p in pods if p.waiting_reason == "CrashLoopBackOff"]
    if not looping:
        return None
    return Issue(
        fingerprint=_fingerprint(cluster_id, wl.namespace, wl.kind or "Pod", wl.name, "crashloop"),
        rule_id="crashloop",
        cluster_id=cluster_id,
        namespace=wl.namespace,
        severity="critical",
        title=f"{wl.kind}/{wl.name}: CrashLoopBackOff",
        detail="컨테이너가 반복 재시작 중입니다. 로그/이벤트 확인 필요(자동 조치 없음, 수동 대응).",
        evidence={"pods": [{"name": p.name, "restarts": p.restart_count} for p in looping[:5]]},
        suggested_action=None,  # 진단 전용
    )


def detect_pod_spread(cluster_id: str, wl: Workload) -> Issue | None:
    """규칙 #5(기능8): 파드가 2개 미만 노드에 몰림 → topologySpreadConstraints 주입 제안.

    단일 replica·기존 분산제약 보유·비대상 kind 는 제외(덮어쓰기 금지). 실제 labelSelector 는
    agent 가 적용 시 live selector 로 채운다.
    """
    if wl.kind not in ("Deployment", "StatefulSet"):
        return None
    if wl.replicas_desired < 2:
        return None
    if wl.has_spread_constraints:
        return None
    if wl.distinct_nodes >= 2:
        return None

    return Issue(
        fingerprint=_fingerprint(cluster_id, wl.namespace, wl.kind, wl.name, "spread"),
        rule_id="pod-spread",
        cluster_id=cluster_id,
        namespace=wl.namespace,
        severity="warn",
        title=f"{wl.kind}/{wl.name}: 파드가 {wl.distinct_nodes}개 노드에만 배치(≥2 권장)",
        detail=(f"replicas={wl.replicas_desired} 인데 {wl.distinct_nodes}개 노드에 몰려 있습니다. "
                "노드 장애 시 가용성 위험 — topologySpreadConstraints 주입을 제안합니다."),
        evidence={"replicas_desired": wl.replicas_desired, "distinct_nodes": wl.distinct_nodes},
        suggested_action=SuggestedAction(
            action_type="inject-spread",
            target_kind=wl.kind,
            target_name=wl.name,
            namespace=wl.namespace,
            # labelSelector 는 agent 가 live selector 로 채움. 여기선 정책 파라미터만.
            patch={"topologyKey": "kubernetes.io/hostname", "maxSkew": 1,
                   "whenUnsatisfiable": "ScheduleAnyway"},
            patch_type="strategic",
            risk_tier="med",
            auto_apply=True,
        ),
    )


# ── 로드맵 스텁 ──────────────────────────────────────────────────────────────
# detect_unschedulable       규칙 #6  (Phase 4+)
# detect_image_pull_backoff  규칙 #8  (Phase 4+)
