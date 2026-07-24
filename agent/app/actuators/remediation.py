"""명령 실행기. hub 명령을 받아 scope 재검증 후 클러스터에 적용.

방어적 심층방어: hub 를 맹신하지 않고 적용 전 대상 scope 를 재확인한다.
멱등 원장(command_id)으로 재전달을 no-op 처리한다.
Phase 2+ 에서 mode 재검증 + capability_sig 검증 + quota 체크를 강화한다.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.kube import KubeClient

log = logging.getLogger("canggu.agent.remediation")

# 워크로드 PodTemplate 리소스 패치 계열 액션(엔진 규칙 + 수동 편집).
_SUPPORTED_ACTIONS = {"rightsize", "bump-memory-limit", "bump-cpu-limit", "manual-edit"}
_SUPPORTED_KINDS = {"Deployment", "StatefulSet"}


class Actuator:
    def __init__(self, kube: KubeClient) -> None:
        self.kube = kube
        self._applied: dict[str, dict] = {}  # command_id -> result (멱등 원장)

    def handle(self, cmd: dict) -> dict:
        cid = cmd.get("command_id", "")
        if cid in self._applied:
            log.info("멱등 원장 히트, 재전달 no-op: %s", cid)
            return self._applied[cid]

        result = self._apply(cmd)
        self._applied[cid] = result
        return result

    def _result(self, cmd: dict, phase: str, **kw) -> dict:
        return {
            "type": "result",
            "command_id": cmd.get("command_id", ""),
            "command_seq": cmd.get("command_seq", 0),
            "phase": phase,
            "resource_version_before": kw.get("rv_before", ""),
            "resource_version_after": kw.get("rv_after", ""),
            "error": kw.get("error", ""),
            "payload": kw.get("payload", ""),
        }

    def _apply(self, cmd: dict) -> dict:
        s = get_settings()

        # ── scope 재검증 ──
        if cmd.get("cluster_id") != s.cluster_id:
            log.warning("scope 불일치: cmd.cluster_id=%s != %s", cmd.get("cluster_id"), s.cluster_id)
            return self._result(cmd, "SKIPPED_SCOPE", error="cluster_id mismatch")

        # ── kubeconfig 발급(요청-응답형) ──
        if cmd.get("type") == "issue-kubeconfig":
            return self._issue_kubeconfig(cmd)

        # ── 노드 분산 강제(기능8) ──
        if cmd.get("type") == "inject-spread":
            return self._inject_spread(cmd)

        # ── Velero 백업/복구/스케줄(기능10) ──
        if cmd.get("type") in ("velero-backup", "velero-restore",
                               "velero-schedule-create", "velero-schedule-delete"):
            return self._velero(cmd)

        namespace = cmd.get("namespace", "")
        kind = cmd.get("target_kind", "")
        name = cmd.get("target_name", "")
        if not namespace or not name:
            return self._result(cmd, "SKIPPED_SCOPE", error="namespace/name 누락")
        if cmd.get("type") not in _SUPPORTED_ACTIONS:
            return self._result(cmd, "FAILED", error=f"미지원 action: {cmd.get('type')}")
        if kind not in _SUPPORTED_KINDS:
            return self._result(cmd, "FAILED", error=f"미지원 kind: {kind}")

        try:
            rv_before, rv_after = self.kube.apply_patch(
                namespace, kind, name, cmd.get("patch", {}), cmd.get("dry_run", False)
            )
        except Exception as e:  # noqa: BLE001
            log.exception("패치 적용 실패")
            return self._result(cmd, "FAILED", error=str(e))

        log.info("적용 완료 %s/%s (%s) rv %s -> %s dry_run=%s",
                 kind, name, namespace, rv_before, rv_after, cmd.get("dry_run"))
        return self._result(cmd, "APPLIED", rv_before=rv_before, rv_after=rv_after)

    def _inject_spread(self, cmd: dict) -> dict:
        namespace = cmd.get("namespace", "")
        kind = cmd.get("target_kind", "")
        name = cmd.get("target_name", "")
        if not namespace or not name or kind not in _SUPPORTED_KINDS:
            return self._result(cmd, "SKIPPED_SCOPE", error="namespace/kind/name 부적합")
        try:
            rv_b, rv_a, skipped = self.kube.inject_topology_spread(
                namespace, kind, name, cmd.get("patch", {}) or {}, cmd.get("dry_run", False)
            )
        except Exception as e:  # noqa: BLE001
            log.exception("spread 주입 실패")
            return self._result(cmd, "FAILED", error=str(e))
        if skipped:
            log.info("spread 주입 skip %s/%s: %s", kind, name, skipped)
            return self._result(cmd, "FAILED", error=skipped)
        log.info("spread 주입 완료 %s/%s rv %s -> %s dry_run=%s",
                 kind, name, rv_b, rv_a, cmd.get("dry_run"))
        return self._result(cmd, "APPLIED", rv_before=rv_b, rv_after=rv_a)

    def _velero(self, cmd: dict) -> dict:
        params = cmd.get("patch", {}) or {}
        name = params.get("name", "")
        try:
            if cmd.get("type") == "velero-backup":
                ns = cmd.get("namespace", "")
                if not ns or not name:
                    return self._result(cmd, "SKIPPED_SCOPE", error="namespace/name 누락")
                created = self.kube.create_velero_backup(ns, name)
                log.info("velero backup 생성: %s (ns=%s)", created, ns)
            elif cmd.get("type") == "velero-restore":
                backup = params.get("backup_name", "")
                if not backup or not name:
                    return self._result(cmd, "SKIPPED_SCOPE", error="backup_name/name 누락")
                created = self.kube.create_velero_restore(backup, name)
                log.info("velero restore 생성: %s (from %s)", created, backup)
            elif cmd.get("type") == "velero-schedule-create":
                ns, cron = cmd.get("namespace", ""), params.get("cron", "")
                if not ns or not name or not cron:
                    return self._result(cmd, "SKIPPED_SCOPE", error="namespace/name/cron 누락")
                created = self.kube.create_velero_schedule(ns, name, cron)
                log.info("velero schedule 생성: %s (ns=%s cron=%s)", created, ns, cron)
            else:  # velero-schedule-delete
                if not name:
                    return self._result(cmd, "SKIPPED_SCOPE", error="name 누락")
                created = self.kube.delete_velero_schedule(name)
                log.info("velero schedule 삭제: %s", created)
        except Exception as e:  # noqa: BLE001
            log.exception("velero 작업 실패")
            return self._result(cmd, "FAILED", error=str(e))
        return self._result(cmd, "APPLIED", payload=created)

    def _issue_kubeconfig(self, cmd: dict) -> dict:
        s = get_settings()
        namespace = cmd.get("namespace", "")
        params = cmd.get("patch", {}) or {}
        role = params.get("role", "view")
        sa_name = params.get("sa_name") or f"canggu-{role}"
        ttl = int(params.get("ttl_seconds") or s.kubeconfig_default_ttl)
        if not namespace:
            return self._result(cmd, "SKIPPED_SCOPE", error="namespace 누락")
        try:
            kubeconfig = self.kube.issue_namespace_kubeconfig(
                namespace=namespace, role=role, sa_name=sa_name, ttl=ttl,
                server=s.kubeconfig_server, context_name=s.cluster_id,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("kubeconfig 발급 실패")
            return self._result(cmd, "FAILED", error=str(e))
        log.info("kubeconfig 발급: ns=%s role=%s sa=%s ttl=%ss", namespace, role, sa_name, ttl)
        return self._result(cmd, "APPLIED", payload=kubeconfig)
