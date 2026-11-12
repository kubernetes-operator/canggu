"""자동조정 엔진: Detector → Issue → SuggestedAction → Gate(mode) → Command.

순수 로직만 담는다(부수효과 없음). 명령 발송/영속은 agentlink/manager 와 api 계층이 담당.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from app.remediation import rules
from app.schemas import Command, Issue, Mode, TelemetrySnapshot

# 조치(patch) 가능한 종류 — agent RBAC(deployments, statefulsets patch)와 일치시킬 것.
PATCHABLE_KINDS = {"Deployment", "StatefulSet"}
# ACTIVE 모드 자동 적용 대상 종류. StatefulSet 은 롤링 재시작이 상태 서비스에 영향 → 수동 승인만.
AUTO_APPLY_KINDS = {"Deployment"}

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
    pod_owner: dict[tuple[str, str], str] = {}  # (ns, pod) -> "Kind/Name"
    for p in snapshot.pods:
        if p.owner_name:
            pods_by_owner.setdefault((p.namespace, p.owner_name), []).append(p)
            pod_owner[(p.namespace, p.name)] = f"{p.owner_kind}/{p.owner_name}"

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
        for i in candidates:
            if i is not None:
                i.target = i.target or f"{wl.kind}/{wl.name}"
                i.gitops_manager = wl.gitops_manager
                if wl.gitops_manager and i.suggested_action is not None:
                    i.detail += (f" ({wl.gitops_manager} 관리 리소스 — 직접 적용하면 되돌려집니다. "
                                 "git 매니페스트에서 수정하세요)")
                if wl.kind not in PATCHABLE_KINDS and i.suggested_action is not None:
                    # agent 에 patch 권한이 없는 종류(DaemonSet) → 진단 전용으로 강등
                    i.suggested_action = None
                    i.detail += " (DaemonSet 은 자동/원클릭 조정 대상이 아님 — 매니페스트/Helm 값에서 수정)"
                issues.append(i)

    # 이벤트 기반 운영 튜닝 제안(워크로드 상관)
    issues.extend(rules.detect_events(cid, snapshot.events, pod_owner))
    return issues


def should_dispatch(
    fingerprint: str, last_dispatch: dict[str, float], now: float, cooldown: float
) -> bool:
    """안티플래핑: 동일 fingerprint 가 cooldown 이내 재적용되면 False. 허용 시 시각 기록."""
    if now - last_dispatch.get(fingerprint, float("-inf")) < cooldown:
        return False
    last_dispatch[fingerprint] = now
    return True


def is_flapping(recent_applies: int, threshold: int) -> bool:
    """동일 대상·규칙 자동 적용이 창 안에서 threshold 회 이상이면 플래핑.

    같은 patch 를 반복 적용해야 한다는 것은 다른 컨트롤러(GitOps selfHeal, 오퍼레이터,
    HPA/VPA 등)가 되돌리고 있다는 뜻 → 자동 적용을 멈추고 사람에게 넘긴다.
    """
    return threshold > 0 and recent_applies >= threshold


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
        if (not sa.auto_apply or sa.risk_tier == "high"
                or sa.target_kind not in AUTO_APPLY_KINDS or issue.gitops_manager):
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
