"""Operating 뷰 상관 로직 단위 테스트."""

from app.core.operating import build_operating
from app.remediation import engine
from app.schemas import Container, Event, Pod, ResourceQ, TelemetrySnapshot, Workload


def _snap() -> TelemetrySnapshot:
    return TelemetrySnapshot(
        cluster_id="c1",
        namespaces=["a", "b"],
        workloads=[
            Workload(namespace="a", kind="Deployment", name="api", replicas_desired=2,
                     replicas_ready=1, distinct_nodes=1,
                     containers=[Container(name="api",
                                           requests=ResourceQ(cpu="100m", memory="100Mi"),
                                           usage_p95=ResourceQ(cpu="150m", memory="50Mi"))]),
            Workload(namespace="a", kind="Deployment", name="quiet", replicas_desired=1,
                     replicas_ready=1, distinct_nodes=1),
            Workload(namespace="b", kind="StatefulSet", name="db", replicas_desired=1,
                     replicas_ready=1, distinct_nodes=1),
        ],
        pods=[
            Pod(namespace="a", name="api-x1", node="n1", phase="Running", restart_count=4,
                waiting_reason="CrashLoopBackOff", owner_kind="Deployment", owner_name="api"),
            Pod(namespace="a", name="api-x2", node="n1", phase="Running",
                owner_kind="Deployment", owner_name="api"),
            Pod(namespace="a", name="quiet-1", node="n1", phase="Running",
                owner_kind="Deployment", owner_name="quiet"),
            Pod(namespace="b", name="db-0", node="n2", phase="Running",
                owner_kind="StatefulSet", owner_name="db"),
        ],
        events=[
            Event(namespace="a", type="Warning", reason="Unhealthy", involved_kind="Pod",
                  involved_name="api-x1", count=3, last_seen="2026-11-12T00:00:00Z"),
            Event(namespace="a", type="Warning", reason="FailedCreate",
                  involved_kind="ReplicaSet", involved_name="api-7d9f8c6b5", count=1),
            Event(namespace="b", type="Warning", reason="FailedMount", involved_kind="Pod",
                  involved_name="db-0", count=2),
        ],
    )


def test_operating_correlates_pods_events_issues():
    snap = _snap()
    issues = engine.evaluate(snap, rightsize_factor=1.5)
    out = build_operating(snap, issues, None)
    rows = {(r["namespace"], r["name"]): r for r in out["workloads"]}

    api = rows[("a", "api")]
    assert api["pods"]["total"] == 2 and api["pods"]["restarts"] == 4
    assert api["pods"]["problem_count"] == 1
    assert {e["reason"] for e in api["events"]} == {"Unhealthy", "FailedCreate"}
    assert any(i["rule_id"] == "crashloop" for i in api["issues"]) or api["issues"]
    assert api["max_usage_ratio"] == 1.5
    assert api["status"] in ("warn", "critical")

    assert rows[("a", "quiet")]["status"] == "ok"
    assert rows[("b", "db")]["event_count"] == 2
    # 정렬: 문제 워크로드가 ok 보다 앞
    assert out["workloads"][-1]["status"] == "ok"


def test_operating_namespace_filter():
    snap = _snap()
    out = build_operating(snap, engine.evaluate(snap, 1.5), "b")
    assert [r["name"] for r in out["workloads"]] == ["db"]
    assert sum(out["counts"].values()) == 1


def test_issue_target_populated():
    snap = _snap()
    for i in engine.evaluate(snap, 1.5):
        assert i.target, i.rule_id
