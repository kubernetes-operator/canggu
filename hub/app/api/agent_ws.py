"""agent 접속용 WebSocket 엔드포인트 (proto Connect 스트림의 Phase 0 폴백 전송)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agentlink.manager import hub
from app.config import get_settings
from app.schemas import CommandResult, Hello, TelemetrySnapshot

router = APIRouter()
log = logging.getLogger("canggu.agentlink")


@router.websocket("/agentlink/ws")
async def agentlink_ws(ws: WebSocket) -> None:
    await ws.accept()
    settings = get_settings()
    cluster_id: str | None = None
    try:
        # 첫 프레임은 반드시 hello (인증).
        first = await ws.receive_json()
        if first.get("type") != "hello":
            await ws.close(code=4400, reason="expected hello")
            return
        hello = Hello(**{k: v for k, v in first.items() if k != "type"})

        if hello.auth_token != settings.agent_shared_token:
            log.warning("agent %s 인증 실패", hello.cluster_id)
            await ws.close(code=4401, reason="unauthorized")
            return

        cluster_id = hello.cluster_id
        await hub.register_agent(hello, ws)
        await ws.send_json({"type": "ack", "message": f"welcome {cluster_id}"})
        log.info("agent 접속: %s (v%s, metrics=%s)", cluster_id, hello.agent_version,
                 hello.metrics_available)

        while True:
            frame = await ws.receive_json()
            ftype = frame.get("type")
            if ftype == "telemetry":
                snap = TelemetrySnapshot(**{k: v for k, v in frame.items() if k != "type"})
                await hub.on_telemetry(snap)
            elif ftype == "result":
                res = CommandResult(**{k: v for k, v in frame.items() if k != "type"})
                await hub.on_result(res)
            elif ftype == "heartbeat":
                await ws.send_json({"type": "ack", "message": "hb"})
            else:
                log.debug("알 수 없는 프레임 무시: %s", ftype)

    except WebSocketDisconnect:
        log.info("agent 연결 종료: %s", cluster_id)
    except Exception:  # noqa: BLE001
        log.exception("agentlink 오류 (cluster=%s)", cluster_id)
    finally:
        if cluster_id:
            await hub.unregister_agent(cluster_id, ws)
