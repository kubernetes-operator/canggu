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


def test_operating_includes_uncollected_owner_kinds():
    snap = _snap()
    snap.pods.append(Pod(namespace="b", name="ds-x", node="n1", phase="Running",
                         restart_count=2, owner_kind="DaemonSet", owner_name="agent"))
    snap.pods.append(Pod(namespace="b", name="static-n1", node="n1", phase="Running",
                         owner_kind="Node", owner_name="n1"))
    out = build_operating(snap, engine.evaluate(snap, 1.5), None)
    rows = {(r["namespace"], r["kind"], r["name"]): r for r in out["workloads"]}
    ds = rows[("b", "DaemonSet", "agent")]
    assert ds["partial"] and ds["pods"]["total"] == 1 and ds["pods"]["restarts"] == 2
    assert not any(k[1] == "Node" for k in rows)
    assert rows[("a", "Deployment", "api")]["partial"] is False


def test_noise_events_excluded_from_status():
    from app.core.operating import parse_noise_reasons

    snap = _snap()
    snap.events.append(Event(namespace="a", type="Warning", reason="DNSConfigForming",
                             involved_kind="Pod", involved_name="quiet-1", count=999))
    noise = parse_noise_reasons(" DNSConfigForming , ")
    assert noise == {"DNSConfigForming"}

    rows = {r["name"]: r for r in build_operating(snap, [], None, noise)["workloads"]}
    quiet = rows["quiet"]
    assert quiet["status"] == "ok"
    assert quiet["event_count"] == 0 and quiet["noise_event_count"] == 999
    assert quiet["events"][0]["noise"] is True

    # 제외 목록이 없으면 기존대로 경고로 취급
    rows = {r["name"]: r for r in build_operating(snap, [], None)["workloads"]}
    assert rows["quiet"]["status"] == "warn" and rows["quiet"]["event_count"] == 999


def test_restart_history_tracks_only_increases():
    from app.core.operating import recent_restarts, update_restart_history

    h: dict = {}
    p = Pod(namespace="a", name="x", phase="Running", restart_count=10)
    update_restart_history(h, [p], now=0)          # 최초 관측: 기준선
    assert recent_restarts(h, now=10, window=3600) == set()
    update_restart_history(h, [p], now=100)        # 변화 없음
    assert recent_restarts(h, now=100, window=3600) == set()
    p2 = p.model_copy(update={"restart_count": 11})
    update_restart_history(h, [p2], now=200)       # 증가 → 최근 재기동
    assert recent_restarts(h, now=300, window=3600) == {("a", "x")}
    assert recent_restarts(h, now=200 + 3601, window=3600) == set()  # 창 밖
    update_restart_history(h, [], now=400)         # 사라진 Pod 제거
    assert h == {}


def test_cumulative_restarts_ignored_with_history():
    snap = _snap()
    snap.pods.append(Pod(namespace="a", name="quiet-2", node="n2", phase="Running",
                         restart_count=10, owner_kind="Deployment", owner_name="quiet"))
    rows = {r["name"]: r for r in build_operating(snap, [], None, recent_restart_pods=set())["workloads"]}
    assert rows["quiet"]["status"] == "ok" and rows["quiet"]["pods"]["restarts"] == 10
    # CrashLoop 대기 중인 Pod 는 이력과 무관하게 문제
    assert rows["api"]["pods"]["problem_count"] == 1

    rows = {r["name"]: r for r in build_operating(
        snap, [], None, recent_restart_pods={("a", "quiet-2")})["workloads"]}
    assert rows["quiet"]["status"] == "warn" and rows["quiet"]["pods"]["recent_restarts"] == 1
