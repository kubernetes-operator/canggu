"""프론트엔드 라이브 피드 WebSocket (자동조정 활동 실시간). 토큰 인증 + 스코프별 필터.

브라우저 WebSocket 은 헤더를 못 넣으므로 ?token=<jwt> 쿼리로 인증한다.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agentlink.manager import hub
from app.config import get_settings
from app.core import auth

router = APIRouter()


@router.websocket("/live/ws")
async def live_ws(ws: WebSocket) -> None:
    await ws.accept()
    s = get_settings()
    if s.auth_enabled:
        principal = auth.verify_token(ws.query_params.get("token", ""))
        if principal is None:
            await ws.close(code=4401, reason="unauthorized")
            return
    else:
        principal = auth.Principal("dev", "admin", "cluster", "")

    await hub.add_frontend(ws, principal)
    await ws.send_json({"kind": "hello", "message": "canggu live feed"})
    try:
        while True:
            await ws.receive_text()  # 연결 유지용
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove_frontend(ws)
