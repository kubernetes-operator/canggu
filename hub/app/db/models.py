"""SQLAlchemy 모델.

Phase 0 는 아래 테이블만 실사용한다. 나머지(users/web_roles/issues/rules/
suggested_actions/kubeconfig_grants/velero_operations)는 로드맵상 Phase 2~5 에서 추가한다.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)   # cluster_id
    name: Mapped[str] = mapped_column(String(256), default="")
    cluster_mode: Mapped[str] = mapped_column(String(16), default="OBSERVE")  # OBSERVE|ACTIVE
    remediation_frozen: Mapped[bool] = mapped_column(default=False)
    agent_version: Mapped[str] = mapped_column(String(64), default="")
    metrics_available: Mapped[bool] = mapped_column(default=False)
    connected: Mapped[bool] = mapped_column(default=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class NamespaceMode(Base):
    """네임스페이스별 모드 오버라이드. 없으면 클러스터 모드를 상속."""

    __tablename__ = "namespace_modes"
    __table_args__ = (UniqueConstraint("cluster_id", "namespace", name="uq_ns_mode"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("clusters.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(256), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="OBSERVE")


class CommandAudit(Base):
    """모든 자동조정 명령의 감사 원장 (= remediation_audit)."""

    __tablename__ = "commands"

    command_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UUID
    command_seq: Mapped[int] = mapped_column(default=0)
    cluster_id: Mapped[str] = mapped_column(String(128), index=True)
    namespace: Mapped[str] = mapped_column(String(256), default="")
    target_kind: Mapped[str] = mapped_column(String(64), default="")
    target_name: Mapped[str] = mapped_column(String(256), default="")
    rule_id: Mapped[str] = mapped_column(String(64), default="")
    action_type: Mapped[str] = mapped_column(String(64), default="")
    patch: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_tier: Mapped[str] = mapped_column(String(16), default="low")
    dry_run: Mapped[bool] = mapped_column(default=False)
    mode_at_issue: Mapped[str] = mapped_column(String(16), default="OBSERVE")
    issued_by: Mapped[str] = mapped_column(String(128), default="engine")
    phase: Mapped[str] = mapped_column(String(24), default="ISSUED")  # 수명주기 상태
    resource_version_before: Mapped[str] = mapped_column(String(64), default="")
    resource_version_after: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(String(1024), default="")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class User(Base):
    """웹 사용자. role=admin(변경 가능)|viewer(읽기). scope 로 클러스터/NS 제한(향후 강제)."""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(128), primary_key=True)
    pw_hash: Mapped[str] = mapped_column(String(256))
    salt: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin|viewer
    scope_type: Mapped[str] = mapped_column(String(16), default="cluster")  # cluster|namespace
    scope_ref: Mapped[str] = mapped_column(String(256), default="")  # "" = 전체
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KubeconfigGrant(Base):
    """kubeconfig 발급 감사(메타데이터만 — 토큰은 저장하지 않음)."""

    __tablename__ = "kubeconfig_grants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(String(128), index=True)
    namespace: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16))          # admin|edit|view
    sa_name: Mapped[str] = mapped_column(String(256))
    ttl_seconds: Mapped[int] = mapped_column(default=3600)
    issued_by: Mapped[str] = mapped_column(String(128))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ModeEvent(Base):
    """모드 토글 감사."""

    __tablename__ = "mode_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(String(128), index=True)
    namespace: Mapped[str] = mapped_column(String(256), default="")  # "" = 클러스터 레벨
    old_mode: Mapped[str] = mapped_column(String(16), default="")
    new_mode: Mapped[str] = mapped_column(String(16), default="")
    changed_by: Mapped[str] = mapped_column(String(128), default="system")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
