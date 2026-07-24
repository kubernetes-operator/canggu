"""동기 DB 헬퍼. async 핸들러에서는 run_in_threadpool 로 호출한다."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import (
    Cluster,
    CommandAudit,
    KubeconfigGrant,
    ModeEvent,
    NamespaceMode,
    User,
)
from app.db.session import SessionLocal
from app.schemas import Command, Mode


def upsert_cluster_on_connect(
    cluster_id: str, agent_version: str, metrics_available: bool, default_frozen: bool
) -> Cluster:
    with SessionLocal() as db:
        c = db.get(Cluster, cluster_id)
        if c is None:
            c = Cluster(
                id=cluster_id,
                name=cluster_id,
                cluster_mode="OBSERVE",
                remediation_frozen=default_frozen,
            )
            db.add(c)
        c.agent_version = agent_version
        c.metrics_available = metrics_available
        c.connected = True
        c.last_seen = datetime.now(timezone.utc)
        db.commit()
        db.refresh(c)
        return c


def get_user(username: str) -> User | None:
    with SessionLocal() as db:
        return db.get(User, username)


def list_users() -> list[User]:
    with SessionLocal() as db:
        return list(db.scalars(select(User)))


def create_user(
    username: str, pw_hash: str, salt: str, role: str, scope_type: str = "cluster",
    scope_ref: str = "",
) -> None:
    with SessionLocal() as db:
        db.add(User(username=username, pw_hash=pw_hash, salt=salt, role=role,
                    scope_type=scope_type, scope_ref=scope_ref))
        db.commit()


def update_cluster_liveness(cluster_id: str, metrics_available: bool) -> None:
    """텔레메트리 수신 시 last_seen + metrics 가용 여부 갱신."""
    with SessionLocal() as db:
        c = db.get(Cluster, cluster_id)
        if c is not None:
            c.metrics_available = metrics_available
            c.last_seen = datetime.now(timezone.utc)
            db.commit()


def mark_disconnected(cluster_id: str) -> None:
    with SessionLocal() as db:
        c = db.get(Cluster, cluster_id)
        if c is not None:
            c.connected = False
            db.commit()


def get_cluster(cluster_id: str) -> Cluster | None:
    with SessionLocal() as db:
        return db.get(Cluster, cluster_id)


def list_clusters() -> list[Cluster]:
    with SessionLocal() as db:
        return list(db.scalars(select(Cluster)))


def ns_modes(cluster_id: str) -> dict[str, str]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(NamespaceMode).where(NamespaceMode.cluster_id == cluster_id)
        )
        return {r.namespace: r.mode for r in rows}


def cluster_mode_and_frozen(cluster_id: str) -> tuple[Mode, bool]:
    with SessionLocal() as db:
        c = db.get(Cluster, cluster_id)
        if c is None:
            return "OBSERVE", True
        return c.cluster_mode, c.remediation_frozen  # type: ignore[return-value]


def set_cluster_mode(cluster_id: str, mode: Mode, by: str) -> None:
    with SessionLocal() as db:
        c = db.get(Cluster, cluster_id)
        if c is None:
            return
        old = c.cluster_mode
        c.cluster_mode = mode
        db.add(ModeEvent(cluster_id=cluster_id, old_mode=old, new_mode=mode, changed_by=by))
        db.commit()


def set_namespace_mode(cluster_id: str, namespace: str, mode: Mode, by: str) -> None:
    with SessionLocal() as db:
        row = db.scalar(
            select(NamespaceMode).where(
                NamespaceMode.cluster_id == cluster_id, NamespaceMode.namespace == namespace
            )
        )
        old = row.mode if row else ""
        if row is None:
            db.add(NamespaceMode(cluster_id=cluster_id, namespace=namespace, mode=mode))
        else:
            row.mode = mode
        db.add(
            ModeEvent(
                cluster_id=cluster_id, namespace=namespace, old_mode=old, new_mode=mode, changed_by=by
            )
        )
        db.commit()


def set_frozen(cluster_id: str, frozen: bool) -> None:
    with SessionLocal() as db:
        c = db.get(Cluster, cluster_id)
        if c is not None:
            c.remediation_frozen = frozen
            db.commit()


def record_command(cmd: Command, rule_id: str, risk_tier: str, mode_at_issue: str) -> None:
    with SessionLocal() as db:
        db.add(
            CommandAudit(
                command_id=cmd.command_id,
                command_seq=cmd.command_seq,
                cluster_id=cmd.cluster_id,
                namespace=cmd.namespace,
                target_kind=cmd.target_kind,
                target_name=cmd.target_name,
                rule_id=rule_id,
                action_type=cmd.type,
                patch=cmd.patch,
                risk_tier=risk_tier,
                dry_run=cmd.dry_run,
                mode_at_issue=mode_at_issue,
                issued_by=cmd.issued_by,
                phase="ISSUED",
            )
        )
        db.commit()


def update_command_phase(
    command_id: str,
    phase: str,
    rv_before: str = "",
    rv_after: str = "",
    error: str = "",
) -> None:
    with SessionLocal() as db:
        c = db.get(CommandAudit, command_id)
        if c is None:
            return
        c.phase = phase
        if rv_before:
            c.resource_version_before = rv_before
        if rv_after:
            c.resource_version_after = rv_after
        if error:
            c.error = error[:1024]
        if phase in ("APPLIED", "FAILED", "SKIPPED_OBSERVE", "SKIPPED_SCOPE", "EXPIRED"):
            c.resolved_at = datetime.now(timezone.utc)
        db.commit()


def record_kubeconfig_grant(
    cluster_id: str, namespace: str, role: str, sa_name: str, ttl: int, issued_by: str
) -> None:
    with SessionLocal() as db:
        db.add(KubeconfigGrant(cluster_id=cluster_id, namespace=namespace, role=role,
                               sa_name=sa_name, ttl_seconds=ttl, issued_by=issued_by))
        db.commit()


def recent_grants(cluster_id: str | None, limit: int = 100) -> list[KubeconfigGrant]:
    with SessionLocal() as db:
        stmt = select(KubeconfigGrant).order_by(KubeconfigGrant.issued_at.desc()).limit(limit)
        if cluster_id:
            stmt = stmt.where(KubeconfigGrant.cluster_id == cluster_id)
        return list(db.scalars(stmt))


def recent_commands(cluster_id: str | None, limit: int = 100) -> list[CommandAudit]:
    with SessionLocal() as db:
        stmt = select(CommandAudit).order_by(CommandAudit.issued_at.desc()).limit(limit)
        if cluster_id:
            stmt = stmt.where(CommandAudit.cluster_id == cluster_id)
        return list(db.scalars(stmt))
