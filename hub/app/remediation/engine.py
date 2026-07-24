"""자동조정 엔진: Detector → Issue → SuggestedAction → Gate(mode) → Command.

순수 로직만 담는다(부수효과 없음). 명령 발송/영속은 agentlink/manager 와 api 계층이 담당.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from app.remediation import rules
from app.schemas import Command, Issue, Mode, TelemetrySnapshot

# effective mode = min(cluster, namespace), OBSERVE < ACTIVE
_MODE_ORDER = {"OBSERVE": 0, "ACTIVE": 1}


def effective_mode(cluster_mode: Mode, ns_mode: Mode | None) -> Mode:
    if ns_mode is None:
        return cluster_mode
    return "OBSERVE" if min(_MODE_ORDER[cluster_mode], _MODE_ORDER[ns_mode]) == 0 else "ACTIVE"


def evaluate(snapshot: TelemetrySnapshot, rightsize_factor: float) -> list[Issue]:
    """스냅샷을 평가해 이슈 목록 생성.

    규칙 #1(OOM)·#2(CPU-limit 근접)·#3(우측정렬)·#4(CrashLoop). pod 는 소유 워크로드별로 묶어
    OOM/CrashLoop detector 에 전달.
    """
    cid = snapshot.cluster_id
    pods_by_owner: dict[tuple[str, str], list] = {}
    for p in snapshot.pods:
        if p.owner_name:
            pods_by_owner.setdefault((p.namespace, p.owner_name), []).append(p)

    issues: list[Issue] = []
    for wl in snapshot.workloads:
        pods = pods_by_owner.get((wl.namespace, wl.name), [])
        candidates = [
            rules.detect_rightsize(cid, wl, rightsize_factor),
            rules.detect_cpu_at_limit(cid, wl),
            rules.detect_oom_killed(cid, wl, pods),
            rules.detect_crashloop(cid, wl, pods),
            rules.detect_pod_spread(cid, wl),
            rules.detect_unschedulable(cid, wl, pods),
            rules.detect_image_pull_backoff(cid, wl, pods),
        ]
        issues.extend(i for i in candidates if i is not None)
    return issues


def apply_rule_config(issues: list[Issue], config: dict[str, dict]) -> list[Issue]:
    """런타임 규칙 설정 적용: 비활성 규칙 이슈 제거 + auto_apply 오버라이드."""
    out: list[Issue] = []
    for i in issues:
        cfg = config.get(i.rule_id, {})
        if cfg.get("enabled", True) is False:
            continue
        override = cfg.get("auto_apply", "default")
        if i.suggested_action is not None and override in ("on", "off"):
            i.suggested_action.auto_apply = override == "on"
        out.append(i)
    return out


def plan_dispatch(
    issues: list[Issue],
    *,
    cluster_mode: Mode,
    ns_mode_of: Callable[[str], Mode | None],
    frozen: bool,
    seq_start: int,
) -> tuple[list[Issue], list[Command]]:
    """모드 게이트 적용.

    - frozen 이면 전부 suggested_only.
    - effective=OBSERVE 이면 suggested_only(제안만).
    - effective=ACTIVE 이고 auto_apply 이며 risk_tier != high 이면 auto_dispatched(+Command).
    - high-risk 는 manual_required.
    반환: (disposition 갱신된 issues, 발송할 Command 목록)
    """
    commands: list[Command] = []
    seq = seq_start

    for issue in issues:
        sa = issue.suggested_action
        if sa is None:
            issue.disposition = "suggested_only"
            continue

        eff = effective_mode(cluster_mode, ns_mode_of(issue.namespace))

        if frozen or eff == "OBSERVE":
            issue.disposition = "suggested_only"
            continue
        if not sa.auto_apply or sa.risk_tier == "high":
            issue.disposition = "manual_required"
            continue

        seq += 1
        commands.append(
            Command(
                command_id=str(uuid.uuid4()),
                command_seq=seq,
                cluster_id=issue.cluster_id,
                namespace=issue.namespace,
                target_kind=sa.target_kind,
                target_name=sa.target_name,
                type=sa.action_type,
                patch=sa.patch,
                patch_type=sa.patch_type,
                dry_run=False,
                issued_by="engine",
            )
        )
        issue.disposition = "auto_dispatched"

    return issues, commands
