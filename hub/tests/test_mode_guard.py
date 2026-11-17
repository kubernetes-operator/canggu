"""ACTIVE 안전장치: 기한 초과 자동 복귀 · 공용 admin ACTIVE 차단 · 모드 이력."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import clusters as clusters_api
from app.config import get_settings
from app.core import auth
from app.db import crud, models
from app.db.models import Cluster, ModeEvent

SessionLocal = None


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    global SessionLocal
    eng = create_engine(f"sqlite:///{tmp_path}/t.db", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=eng)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(crud, "SessionLocal", SessionLocal)
    with SessionLocal() as db:
        db.add(Cluster(id="c1", name="c1", cluster_mode="OBSERVE"))
        db.commit()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _backdate_last_event(seconds: float):
    with SessionLocal() as db:
        ev = db.query(ModeEvent).order_by(ModeEvent.id.desc()).first()
        ev.at = _now() - timedelta(seconds=seconds)
        db.commit()


def _mode():
    return crud.cluster_mode_and_frozen("c1")[0]


def test_cluster_active_reverts_after_max():
    crud.set_cluster_mode("c1", "ACTIVE", "alice")
    assert crud.expire_active_modes(3600) == []          # 아직 기한 전
    assert _mode() == "ACTIVE"
    _backdate_last_event(3601)
    expired = crud.expire_active_modes(3600)
    assert [(e["cluster_id"], e["namespace"]) for e in expired] == [("c1", "")]
    assert _mode() == "OBSERVE"
    last = crud.list_mode_events("c1", 1)[0]
    assert last["new_mode"] == "OBSERVE" and last["changed_by"] == "system:auto-revert"


def test_namespace_active_reverts_and_disabled_when_zero():
    crud.set_namespace_mode("c1", "team-a", "ACTIVE", "alice")
    _backdate_last_event(7200)
    assert crud.expire_active_modes(0) == []             # 0 = 자동 복귀 끔
    expired = crud.expire_active_modes(3600)
    assert [(e["cluster_id"], e["namespace"]) for e in expired] == [("c1", "team-a")]
    assert crud.ns_modes("c1")["team-a"] == "OBSERVE"


def test_active_without_history_is_reverted():
    """이력 없이 ACTIVE 인 상태(사고 이전 데이터)도 기한 초과로 본다."""
    with SessionLocal() as db:
        db.get(Cluster, "c1").cluster_mode = "ACTIVE"
        db.commit()
    assert len(crud.expire_active_modes(3600)) == 1
    assert _mode() == "OBSERVE"


def test_reactivation_resets_since():
    crud.set_cluster_mode("c1", "ACTIVE", "alice")
    _backdate_last_event(5000)
    crud.set_cluster_mode("c1", "OBSERVE", "alice")
    crud.set_cluster_mode("c1", "ACTIVE", "bob")         # 새로 켬 → 기준 시각 갱신
    assert crud.expire_active_modes(3600) == []


def _principal(name):
    return auth.Principal(username=name, role="admin", scope_type="cluster", scope_ref="")


def test_shared_admin_cannot_activate(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "mode_active_require_personal", True)
    with pytest.raises(HTTPException) as ei:
        clusters_api._guard_active(_principal(s.admin_user), "ACTIVE")
    assert ei.value.status_code == 403
    clusters_api._guard_active(_principal(s.admin_user), "OBSERVE")   # 끄는 것은 허용
    clusters_api._guard_active(_principal("alice"), "ACTIVE")         # 개인 계정 허용
    monkeypatch.setattr(s, "mode_active_require_personal", False)
    clusters_api._guard_active(_principal(s.admin_user), "ACTIVE")


def test_expire_loop_broadcasts(monkeypatch):
    from app import main
    from app.agentlink.manager import hub

    sent = []

    async def fake_broadcast(ev):
        sent.append(ev)

    monkeypatch.setattr(hub, "broadcast_event", fake_broadcast)
    crud.set_cluster_mode("c1", "ACTIVE", "alice")
    _backdate_last_event(99999)
    asyncio.run(main.expire_active_once())
    assert sent and sent[0]["mode"] == "OBSERVE" and sent[0]["reason"] == "active_expired"
