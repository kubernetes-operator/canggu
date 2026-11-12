"""플래핑 차단: DB 감사 원장 기반 반복 자동 적용 카운트 + manager 차단 동작."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import crud, models
from app.db.models import CommandAudit

SessionLocal = None


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """테스트 전용 SQLite — 실제 hub DB 를 건드리지 않도록 crud.SessionLocal 교체."""
    global SessionLocal
    eng = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=eng)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(crud, "SessionLocal", SessionLocal)


def _add(cid: str, ago_s: float, phase="APPLIED", issued_by="engine", dry=False, name="api"):
    with SessionLocal() as db:
        db.add(CommandAudit(
            command_id=f"{cid}-{name}-{ago_s}-{phase}-{issued_by}-{dry}", cluster_id=cid,
            namespace="team-a", target_kind="Deployment", target_name=name,
            rule_id="resource-rightsize", dry_run=dry, issued_by=issued_by, phase=phase,
            issued_at=datetime.now(timezone.utc) - timedelta(seconds=ago_s),
        ))
        db.commit()


def _count(cid: str, name="api") -> int:
    return crud.recent_auto_applies(cid, "team-a", "Deployment", name, "resource-rightsize", 3600)


def test_recent_auto_applies_counts_only_engine_real_applies_in_window():
    cid = "flap-a"
    _add(cid, 60)
    _add(cid, 400)
    _add(cid, 7200)                      # 창 밖
    _add(cid, 30, phase="FAILED")        # 실패 제외
    _add(cid, 30, issued_by="admin")     # 수동 제외
    _add(cid, 30, dry=True)              # dry-run 제외
    _add(cid, 30, name="other")          # 다른 대상
    assert _count(cid) == 2


def test_manager_blocks_flapping_dispatch(monkeypatch):
    from app.agentlink.manager import Hub
    from app.schemas import Container, ResourceQ, TelemetrySnapshot, Workload

    cid = "flap-b"
    for ago in (60, 400, 700):
        _add(cid, ago)

    hub = Hub()
    sent: list = []

    async def fake_dispatch(cmd, issue, mode):
        sent.append(cmd)

    hub._dispatch = fake_dispatch  # type: ignore[method-assign]

    monkeypatch.setattr(crud, "cluster_mode_and_frozen", lambda c: ("ACTIVE", False))
    monkeypatch.setattr(crud, "ns_modes", lambda c: {})
    monkeypatch.setattr(crud, "get_rule_configs",
                        lambda: {"resource-rightsize": {"auto_apply": "on"}})
    monkeypatch.setattr(crud, "update_cluster_liveness", lambda *a: None)

    snap = TelemetrySnapshot(cluster_id=cid, workloads=[Workload(
        namespace="team-a", kind="Deployment", name="api", replicas_desired=1, replicas_ready=1,
        distinct_nodes=1, containers=[Container(
            name="api", requests=ResourceQ(cpu="100m", memory="128Mi"),
            limits=ResourceQ(cpu="2", memory="2Gi"), usage_p95=ResourceQ(cpu="450m", memory="500Mi"))],
    )])
    asyncio.run(hub.on_telemetry(snap))
    rs = next(i for i in hub.issues[cid] if i.rule_id == "resource-rightsize")
    assert sent == [] and rs.disposition == "flapping"


def test_manager_dispatches_when_not_flapping(monkeypatch):
    """대조군: 이력이 임계 미만이면 정상 발송."""
    from app.agentlink.manager import Hub
    from app.schemas import Container, ResourceQ, TelemetrySnapshot, Workload

    cid = "flap-c"
    _add(cid, 60)
    hub = Hub()
    sent: list = []

    async def fake_dispatch(cmd, issue, mode):
        sent.append(cmd)

    hub._dispatch = fake_dispatch  # type: ignore[method-assign]
    monkeypatch.setattr(crud, "cluster_mode_and_frozen", lambda c: ("ACTIVE", False))
    monkeypatch.setattr(crud, "ns_modes", lambda c: {})
    monkeypatch.setattr(crud, "get_rule_configs",
                        lambda: {"resource-rightsize": {"auto_apply": "on"}})
    monkeypatch.setattr(crud, "update_cluster_liveness", lambda *a: None)
    snap = TelemetrySnapshot(cluster_id=cid, workloads=[Workload(
        namespace="team-a", kind="Deployment", name="api", replicas_desired=1, replicas_ready=1,
        distinct_nodes=1, containers=[Container(
            name="api", requests=ResourceQ(cpu="100m", memory="128Mi"),
            limits=ResourceQ(cpu="2", memory="2Gi"), usage_p95=ResourceQ(cpu="450m", memory="500Mi"))],
    )])
    asyncio.run(hub.on_telemetry(snap))
    assert len(sent) == 1
