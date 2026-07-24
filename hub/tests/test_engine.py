"""자동조정 엔진 단위 테스트 (규칙 #3 + 모드 게이트)."""

from app.remediation import engine
from app.remediation.quantity import format_cpu, format_mem, parse_cpu, parse_mem
from app.schemas import Container, ResourceQ, TelemetrySnapshot, Workload


def _snap_with_underprovisioned() -> TelemetrySnapshot:
    return TelemetrySnapshot(
        cluster_id="c1",
        namespaces=["team-a"],
        workloads=[
            Workload(
                namespace="team-a",
                kind="Deployment",
                name="api",
                replicas_desired=3,
                replicas_ready=3,
                distinct_nodes=1,
                containers=[
                    Container(
                        name="api",
                        requests=ResourceQ(cpu="100m", memory="128Mi"),
                        limits=ResourceQ(cpu="200m", memory="256Mi"),
                        usage_p95=ResourceQ(cpu="450m", memory="500Mi"),  # requests 대폭 초과
                    )
                ],
            )
        ],
    )


def test_quantity_roundtrip():
    assert parse_cpu("250m") == 0.25
    assert parse_cpu("1") == 1.0
    assert parse_mem("256Mi") == 256 * 1024**2
    assert format_cpu(0.35) == "350m"
    assert format_mem(256 * 1024**2) == "256Mi"


def _by_rule(issues, rule_id):
    return next((i for i in issues if i.rule_id == rule_id), None)


def test_rightsize_detected():
    issues = engine.evaluate(_snap_with_underprovisioned(), rightsize_factor=1.5)
    issue = _by_rule(issues, "resource-rightsize")
    assert issue is not None and issue.suggested_action is not None
    reqs = issue.suggested_action.patch["spec"]["template"]["spec"]["containers"][0]["resources"][
        "requests"
    ]
    # p95=450m * 1.2 headroom = 540m
    assert reqs["cpu"] == "540m"


def test_cpu_at_limit_detected():
    # usage 450m >= limit 200m * 0.9 → cpu-throttling 규칙
    issue = _by_rule(engine.evaluate(_snap_with_underprovisioned(), 1.5), "cpu-throttling")
    assert issue is not None
    cpu = issue.suggested_action.patch["spec"]["template"]["spec"]["containers"][0]["resources"][
        "limits"
    ]["cpu"]
    assert cpu == "300m"  # 200m * 1.5


def test_oom_detected():
    from app.schemas import Pod

    snap = _snap_with_underprovisioned()
    snap.pods = [
        Pod(namespace="team-a", name="api-x", owner_kind="Deployment", owner_name="api",
            last_terminated_reason="OOMKilled")
    ]
    issue = _by_rule(engine.evaluate(snap, 1.5), "oom-killed")
    assert issue is not None and issue.severity == "critical"
    mem = issue.suggested_action.patch["spec"]["template"]["spec"]["containers"][0]["resources"][
        "limits"
    ]["memory"]
    assert mem == "384Mi"  # 256Mi * 1.5


def test_crashloop_diagnostic_only():
    from app.schemas import Pod

    snap = _snap_with_underprovisioned()
    snap.pods = [
        Pod(namespace="team-a", name="api-y", owner_kind="Deployment", owner_name="api",
            waiting_reason="CrashLoopBackOff", restart_count=7)
    ]
    issue = _by_rule(engine.evaluate(snap, 1.5), "crashloop")
    assert issue is not None
    assert issue.suggested_action is None  # 진단 전용, 자동 조치 없음


def test_wellprovisioned_no_rightsize():
    snap = _snap_with_underprovisioned()
    # 사용량을 requests 이하로 낮추면 rightsize/cpu 이슈 없음(pod-spread 는 별개)
    snap.workloads[0].containers[0].usage_p95 = ResourceQ(cpu="80m", memory="100Mi")
    issues = engine.evaluate(snap, rightsize_factor=1.5)
    assert _by_rule(issues, "resource-rightsize") is None
    assert _by_rule(issues, "cpu-throttling") is None


def test_no_metrics_no_rightsize():
    """메트릭(usage_p95) 없으면 rightsize/cpu 조치하지 않는다 (fail-safe)."""
    snap = _snap_with_underprovisioned()
    snap.workloads[0].containers[0].usage_p95 = ResourceQ()
    issues = engine.evaluate(snap, rightsize_factor=1.5)
    assert _by_rule(issues, "resource-rightsize") is None
    assert _by_rule(issues, "cpu-throttling") is None


def test_pod_spread_detected():
    # replicas 3, distinct_nodes 1, 제약 없음 → pod-spread 규칙
    issue = _by_rule(engine.evaluate(_snap_with_underprovisioned(), 1.5), "pod-spread")
    assert issue is not None and issue.suggested_action.action_type == "inject-spread"
    assert issue.suggested_action.patch["topologyKey"] == "kubernetes.io/hostname"


def test_pod_spread_skipped_when_constraints_exist():
    snap = _snap_with_underprovisioned()
    snap.workloads[0].has_spread_constraints = True
    assert _by_rule(engine.evaluate(snap, 1.5), "pod-spread") is None


def test_pod_spread_skipped_single_replica():
    snap = _snap_with_underprovisioned()
    snap.workloads[0].replicas_desired = 1
    assert _by_rule(engine.evaluate(snap, 1.5), "pod-spread") is None


def test_apply_rule_config_disable_drops_issue():
    issues = engine.evaluate(_snap_with_underprovisioned(), 1.5)
    cfg = {"resource-rightsize": {"enabled": False, "auto_apply": "default"}}
    out = engine.apply_rule_config(issues, cfg)
    assert _by_rule(out, "resource-rightsize") is None
    assert _by_rule(out, "pod-spread") is not None  # 다른 규칙은 유지


def test_apply_rule_config_auto_apply_override():
    issues = engine.evaluate(_snap_with_underprovisioned(), 1.5)
    cfg = {"pod-spread": {"enabled": True, "auto_apply": "off"}}
    out = engine.apply_rule_config(issues, cfg)
    sp = _by_rule(out, "pod-spread")
    assert sp is not None and sp.suggested_action.auto_apply is False


def test_unschedulable_and_imagepull_diagnostic():
    from app.schemas import Pod

    snap = _snap_with_underprovisioned()
    snap.pods = [
        Pod(namespace="team-a", name="api-p", owner_kind="Deployment", owner_name="api",
            phase="Pending", unschedulable=True),
        Pod(namespace="team-a", name="api-i", owner_kind="Deployment", owner_name="api",
            waiting_reason="ImagePullBackOff"),
    ]
    issues = engine.evaluate(snap, 1.5)
    u = _by_rule(issues, "unschedulable")
    i = _by_rule(issues, "image-pull-backoff")
    assert u is not None and u.suggested_action is None
    assert i is not None and i.suggested_action is None


def test_effective_mode():
    assert engine.effective_mode("ACTIVE", None) == "ACTIVE"
    assert engine.effective_mode("ACTIVE", "OBSERVE") == "OBSERVE"  # observe 우선
    assert engine.effective_mode("OBSERVE", "ACTIVE") == "OBSERVE"
    assert engine.effective_mode("ACTIVE", "ACTIVE") == "ACTIVE"


def test_observe_suggests_only():
    issues = engine.evaluate(_snap_with_underprovisioned(), 1.5)
    issues, commands = engine.plan_dispatch(
        issues, cluster_mode="OBSERVE", ns_mode_of=lambda ns: None, frozen=False, seq_start=0
    )
    assert commands == []
    assert issues[0].disposition == "suggested_only"


def test_active_dispatches():
    issues = engine.evaluate(_snap_with_underprovisioned(), 1.5)
    issues, commands = engine.plan_dispatch(
        issues, cluster_mode="ACTIVE", ns_mode_of=lambda ns: None, frozen=False, seq_start=0
    )
    # rightsize + cpu-throttling 둘 다 auto_apply
    assert len(commands) >= 1
    assert any(c.type == "rightsize" for c in commands)
    assert all(i.disposition == "auto_dispatched" for i in issues if i.suggested_action)


def test_frozen_blocks_dispatch():
    issues = engine.evaluate(_snap_with_underprovisioned(), 1.5)
    issues, commands = engine.plan_dispatch(
        issues, cluster_mode="ACTIVE", ns_mode_of=lambda ns: None, frozen=True, seq_start=0
    )
    assert commands == []
    assert issues[0].disposition == "suggested_only"
