"""agent 연결 관리 + 텔레메트리 처리 + 명령 디스패치 + 프론트 라이브 피드.

전송은 Phase 0 에서 WebSocket(JSON). Phase 1+ 에 gRPC 스트림으로 승격해도 이 계층 API 는 유지.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import crud
from app.remediation import engine
from app.schemas import Command, CommandResult, Hello, Issue, TelemetrySnapshot


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Hub:
    """싱글턴 상태 보관소."""

    def __init__(self) -> None:
        self.agents: dict[str, WebSocket] = {}
        self.snapshots: dict[str, TelemetrySnapshot] = {}
        self.issues: dict[str, list[Issue]] = {}
        self.frontend: dict[WebSocket, object] = {}  # ws -> Principal (라이브 피드 구독자)
        self._seq: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}  # command_id -> 결과 future(요청-응답형)
        self._last_dispatch: dict[str, float] = {}  # fingerprint -> monotonic ts(안티플래핑)

    # ── agent 수명주기 ──────────────────────────────────────────────────────
    async def register_agent(self, hello: Hello, ws: WebSocket) -> None:
        s = get_settings()
        # 같은 cluster_id 의 기존 연결이 있으면(롤아웃 중 파드 공존 등) 최신 연결이 이긴다.
        old = self.agents.get(hello.cluster_id)
        if old is not None and old is not ws:
            try:
                await old.close(code=4409, reason="superseded by new agent connection")
            except Exception:  # noqa: BLE001
                pass
        self.agents[hello.cluster_id] = ws
        await run_in_threadpool(
            crud.upsert_cluster_on_connect,
            hello.cluster_id,
            hello.agent_version,
            hello.metrics_available,
            s.remediation_default_frozen,
        )
        await self.broadcast_event(
            {"kind": "agent_connected", "cluster_id": hello.cluster_id, "ts": _now_iso()}
        )

    async def unregister_agent(self, cluster_id: str, ws: WebSocket | None = None) -> None:
        # ws 식별자가 일치할 때만 제거한다. 종료 중인 옛 연결이 살아있는 새 연결을
        # 지워버리는 것을 방지(롤아웃 중 동일 cluster_id 공존 대응).
        if ws is not None and self.agents.get(cluster_id) is not ws:
            return
        self.agents.pop(cluster_id, None)
        await run_in_threadpool(crud.mark_disconnected, cluster_id)
        await self.broadcast_event(
            {"kind": "agent_disconnected", "cluster_id": cluster_id, "ts": _now_iso()}
        )

    # ── 텔레메트리 → 엔진 → 디스패치 ─────────────────────────────────────────
    async def on_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self.snapshots[snapshot.cluster_id] = snapshot
        s = get_settings()

        # 연결 후 첫 collect 에서야 metrics 가용 여부가 확정되므로 텔레메트리마다 갱신.
        await run_in_threadpool(
            crud.update_cluster_liveness, snapshot.cluster_id, snapshot.metrics_available
        )

        issues = engine.evaluate(snapshot, s.rightsize_factor)

        rule_cfg = await run_in_threadpool(crud.get_rule_configs)
        issues = engine.apply_rule_config(issues, rule_cfg)

        cluster_mode, frozen = await run_in_threadpool(
            crud.cluster_mode_and_frozen, snapshot.cluster_id
        )
        ns_map = await run_in_threadpool(crud.ns_modes, snapshot.cluster_id)

        seq = self._seq.get(snapshot.cluster_id, 0)
        issues, commands = engine.plan_dispatch(
            issues,
            cluster_mode=cluster_mode,
            ns_mode_of=lambda ns: ns_map.get(ns),  # type: ignore[arg-type]
            frozen=frozen,
            seq_start=seq,
        )
        if commands:
            self._seq[snapshot.cluster_id] = commands[-1].command_seq

        self.issues[snapshot.cluster_id] = issues

        await self.broadcast_event(
            {
                "kind": "telemetry",
                "cluster_id": snapshot.cluster_id,
                "issues": len(issues),
                "pods": len(snapshot.pods),
                "ts": _now_iso(),
            }
        )

        # active + auto_apply 이슈에 대한 명령 발송 (동일 fingerprint 쿨다운으로 플래핑 억제)
        now = time.monotonic()
        auto_issues = [i for i in issues if i.disposition == "auto_dispatched"]
        for cmd, issue in zip(commands, auto_issues):
            if not engine.should_dispatch(
                issue.fingerprint, self._last_dispatch, now, s.remediation_cooldown_seconds
            ):
                issue.disposition = "cooldown"
                continue
            await self._dispatch(cmd, issue, cluster_mode)

    async def _dispatch(self, cmd: Command, issue: Issue, mode_at_issue: str) -> None:
        risk = issue.suggested_action.risk_tier if issue.suggested_action else "low"
        await run_in_threadpool(crud.record_command, cmd, issue.rule_id, risk, mode_at_issue)
        await self.broadcast_event(
            {
                "kind": "command_issued",
                "cluster_id": cmd.cluster_id,
                "namespace": cmd.namespace,
                "target": f"{cmd.target_kind}/{cmd.target_name}",
                "action": cmd.type,
                "command_id": cmd.command_id,
                "ts": _now_iso(),
            }
        )
        await self.send_command(cmd)

    async def send_command(self, cmd: Command) -> bool:
        ws = self.agents.get(cmd.cluster_id)
        if ws is None:
            await run_in_threadpool(
                crud.update_command_phase, cmd.command_id, "FAILED", error="agent not connected"
            )
            return False
        # 주의: Command.type(action) 과 envelope type 충돌 방지 위해 중첩 구조 사용.
        await ws.send_json({"type": "command", "command": cmd.model_dump()})
        await run_in_threadpool(crud.update_command_phase, cmd.command_id, "DELIVERED")
        return True

    async def request_command(self, cmd: Command, timeout: float = 20.0) -> CommandResult:
        """요청-응답형 명령: 발송 후 해당 command_id 의 결과를 대기(kubeconfig 발급 등)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[cmd.command_id] = fut
        try:
            ok = await self.send_command(cmd)
            if not ok:
                raise RuntimeError("agent 미접속")
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(cmd.command_id, None)

    async def on_result(self, result: CommandResult) -> None:
        await run_in_threadpool(
            crud.update_command_phase,
            result.command_id,
            result.phase,
            result.resource_version_before,
            result.resource_version_after,
            result.error,
        )
        fut = self._pending.get(result.command_id)
        if fut is not None and not fut.done():
            fut.set_result(result)
        await self.broadcast_event(
            {
                "kind": "command_result",
                "command_id": result.command_id,
                "phase": result.phase,
                "error": result.error,
                "ts": _now_iso(),
            }
        )

    # ── 프론트엔드 라이브 피드 (스코프별 필터) ───────────────────────────────
    async def add_frontend(self, ws: WebSocket, principal: object) -> None:
        self.frontend[ws] = principal

    def remove_frontend(self, ws: WebSocket) -> None:
        self.frontend.pop(ws, None)

    async def broadcast_event(self, event: dict[str, Any]) -> None:
        from app.core.scope import event_visible

        dead = []
        for ws, principal in list(self.frontend.items()):
            if not event_visible(principal, event):
                continue
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001 — 끊긴 소켓 정리
                dead.append(ws)
        for ws in dead:
            self.frontend.pop(ws, None)


hub = Hub()
