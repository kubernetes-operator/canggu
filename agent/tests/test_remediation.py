"""actuator scope 재검증 + 멱등 원장 테스트 (MOCK kube)."""

import os

os.environ.setdefault("CANGGU_MOCK", "1")
os.environ.setdefault("CANGGU_CLUSTER_ID", "c1")

from app.actuators.remediation import Actuator  # noqa: E402
from app.kube import KubeClient  # noqa: E402


def _cmd(**over) -> dict:
    base = {
        "command_id": "cmd-1",
        "command_seq": 1,
        "cluster_id": "c1",
        "namespace": "team-a",
        "target_kind": "Deployment",
        "target_name": "api",
        "type": "rightsize",
        "patch": {"spec": {}},
        "dry_run": False,
    }
    base.update(over)
    return base


def _actuator() -> Actuator:
    return Actuator(KubeClient())


def test_apply_ok():
    res = _actuator().handle(_cmd())
    assert res["phase"] == "APPLIED"
    assert res["resource_version_after"] == "mock-2"


def test_dry_run_no_change():
    res = _actuator().handle(_cmd(dry_run=True))
    assert res["phase"] == "APPLIED"
    assert res["resource_version_before"] == res["resource_version_after"]


def test_scope_mismatch_cluster():
    res = _actuator().handle(_cmd(cluster_id="other"))
    assert res["phase"] == "SKIPPED_SCOPE"


def test_missing_namespace():
    res = _actuator().handle(_cmd(namespace=""))
    assert res["phase"] == "SKIPPED_SCOPE"


def test_unsupported_action():
    res = _actuator().handle(_cmd(type="delete-everything"))
    assert res["phase"] == "FAILED"


def test_supported_action_types():
    # 엔진 규칙(Phase 2) + 수동 편집 액션이 모두 실행 가능해야 함
    for i, action in enumerate(["bump-memory-limit", "bump-cpu-limit", "manual-edit"]):
        res = _actuator().handle(_cmd(command_id=f"a{i}", type=action))
        assert res["phase"] == "APPLIED", f"{action} -> {res}"


def test_inject_spread_applied():
    res = _actuator().handle(_cmd(
        command_id="sp1", type="inject-spread", target_kind="Deployment",
        target_name="api", namespace="team-a",
        patch={"topologyKey": "kubernetes.io/hostname", "maxSkew": 1,
               "whenUnsatisfiable": "ScheduleAnyway"},
    ))
    assert res["phase"] == "APPLIED"


def test_velero_backup_and_restore():
    a = _actuator()
    b = a.handle(_cmd(command_id="vb1", type="velero-backup", target_kind="Backup",
                      target_name="team-a-x", namespace="team-a", patch={"name": "team-a-x"}))
    assert b["phase"] == "APPLIED" and b["payload"] == "team-a-x"
    r = a.handle(_cmd(command_id="vr1", type="velero-restore", target_kind="Restore",
                      target_name="restore-x", namespace="velero",
                      patch={"name": "restore-x", "backup_name": "team-a-x"}))
    assert r["phase"] == "APPLIED" and r["payload"] == "restore-x"


def test_issue_kubeconfig_returns_payload():
    res = _actuator().handle(_cmd(
        command_id="kc1", type="issue-kubeconfig", target_kind="ServiceAccount",
        target_name="canggu-view", namespace="team-a",
        patch={"role": "view", "sa_name": "canggu-view", "ttl_seconds": 3600},
    ))
    assert res["phase"] == "APPLIED"
    assert "kind: Config" in res["payload"]  # kubeconfig 반환
    assert "team-a" in res["payload"]


def test_idempotent_ledger():
    act = _actuator()
    first = act.handle(_cmd(command_id="dup"))
    second = act.handle(_cmd(command_id="dup", target_name="SHOULD-NOT-REAPPLY"))
    assert first == second  # 재전달은 no-op, 최초 결과 반환
