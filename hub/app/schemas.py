"""agent↔hub JSON 프레임 및 API 스키마 (proto/agent_hub.proto 의 Phase 0 JSON 표현)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["OBSERVE", "ACTIVE"]


# ── 텔레메트리 (agent -> hub) ────────────────────────────────────────────────
class ResourceQ(BaseModel):
    cpu: str = ""
    memory: str = ""


class Container(BaseModel):
    name: str
    requests: ResourceQ = Field(default_factory=ResourceQ)
    limits: ResourceQ = Field(default_factory=ResourceQ)
    usage_p95: ResourceQ = Field(default_factory=ResourceQ)


class Workload(BaseModel):
    namespace: str
    kind: str
    name: str
    replicas_desired: int = 0
    replicas_ready: int = 0
    distinct_nodes: int = 0
    has_spread_constraints: bool = False  # 기존 topologySpread/podAntiAffinity 존재 여부
    containers: list[Container] = Field(default_factory=list)


class Pod(BaseModel):
    namespace: str
    name: str
    node: str = ""
    phase: str = ""
    restart_count: int = 0
    waiting_reason: str = ""
    last_terminated_reason: str = ""
    owner_kind: str = ""
    owner_name: str = ""


class Service(BaseModel):
    namespace: str
    name: str
    type: str = ""
    ready_endpoints: int = 0
    desired_endpoints: int = 0

    @property
    def healthy(self) -> bool:
        return not (self.desired_endpoints > 0 and self.ready_endpoints == 0)


class StorageLink(BaseModel):
    namespace: str
    pvc: str
    pv: str = ""
    storage_class: str = ""
    capacity: str = ""
    bound_workloads: list[str] = Field(default_factory=list)
    phase: str = ""          # Bound | Pending | Lost


class RouteEdge(BaseModel):
    namespace: str
    httproute: str
    gateway: str = ""
    hostname: str = ""
    path: str = ""
    backend_service: str = ""
    backend_port: int = 0
    weight: int = 1


class VeleroBackup(BaseModel):
    name: str
    phase: str = ""              # Completed | InProgress | Failed | PartiallyFailed ...
    included_namespaces: list[str] = Field(default_factory=list)
    created: str = ""
    completed: str = ""
    errors: int = 0
    warnings: int = 0


class VeleroRestore(BaseModel):
    name: str
    backup_name: str = ""
    phase: str = ""
    created: str = ""
    errors: int = 0
    warnings: int = 0


class VeleroSchedule(BaseModel):
    name: str
    cron: str = ""
    included_namespaces: list[str] = Field(default_factory=list)
    paused: bool = False
    last_backup: str = ""


class TelemetrySnapshot(BaseModel):
    cluster_id: str
    generation: int = 0
    metrics_available: bool = False
    velero_installed: bool = False
    namespaces: list[str] = Field(default_factory=list)
    workloads: list[Workload] = Field(default_factory=list)
    pods: list[Pod] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    storage: list[StorageLink] = Field(default_factory=list)
    routes: list[RouteEdge] = Field(default_factory=list)
    velero_backups: list[VeleroBackup] = Field(default_factory=list)
    velero_restores: list[VeleroRestore] = Field(default_factory=list)
    velero_schedules: list[VeleroSchedule] = Field(default_factory=list)


class Hello(BaseModel):
    cluster_id: str
    agent_version: str = ""
    auth_token: str = ""
    last_command_seq: int = 0
    metrics_available: bool = False


# ── 명령 (hub -> agent) ──────────────────────────────────────────────────────
class Command(BaseModel):
    command_id: str
    command_seq: int = 0
    cluster_id: str
    namespace: str = ""
    target_kind: str = ""
    target_name: str = ""
    type: str = ""              # action type, 예: "rightsize"
    patch: dict = Field(default_factory=dict)
    patch_type: Literal["strategic", "merge", "json"] = "strategic"
    dry_run: bool = False
    expected_resource_version: str = ""
    ttl_seconds: int = 120
    issued_by: str = "engine"
    capability_sig: str = ""


class CommandResult(BaseModel):
    command_id: str
    command_seq: int = 0
    phase: Literal[
        "DELIVERED", "APPLIED", "FAILED", "SKIPPED_OBSERVE", "SKIPPED_SCOPE", "EXPIRED"
    ]
    resource_version_before: str = ""
    resource_version_after: str = ""
    error: str = ""
    # 요청-응답형 명령(예: kubeconfig 발급)의 반환 데이터. DB 에 저장하지 않음(민감).
    payload: str = ""


# ── API 응답 (hub -> web) ────────────────────────────────────────────────────
class SuggestedAction(BaseModel):
    action_type: str
    target_kind: str
    target_name: str
    namespace: str
    patch: dict
    patch_type: str = "strategic"
    risk_tier: Literal["low", "med", "high", "info", "warn"] = "low"
    auto_apply: bool = False


class Issue(BaseModel):
    fingerprint: str
    rule_id: str
    cluster_id: str
    namespace: str
    severity: Literal["info", "warn", "critical"] = "info"
    title: str
    detail: str = ""
    evidence: dict = Field(default_factory=dict)
    suggested_action: SuggestedAction | None = None
    # 이 이슈에 대해 엔진이 취한 조치(모드 게이트 결과)
    disposition: Literal["suggested_only", "applied", "auto_dispatched", "manual_required"] = (
        "suggested_only"
    )
