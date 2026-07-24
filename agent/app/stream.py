"""hub 로 outbound WebSocket 연결 (proto Connect 스트림의 Phase 0 폴백).

- 첫 프레임으로 hello(인증) 전송.
- 주기적으로 telemetry push, hub 명령 수신 → actuator 실행 → result 반환.
- 끊기면 지수 백오프+지터로 재연결.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random

import websockets

from app.actuators.remediation import Actuator
from app.collectors.inventory import collect_inventory
from app.config import get_settings
from app.kube import KubeClient

log = logging.getLogger("canggu.agent.stream")


def _touch(path: str) -> None:
    try:
        with open(path, "w") as f:
            f.write("ok")
    except OSError:
        pass


class HubLink:
    def __init__(self, kube: KubeClient, actuator: Actuator) -> None:
        self.kube = kube
        self.actuator = actuator
        self._generation = 0
        self._last_command_seq = 0

    async def run_forever(self) -> None:
        s = get_settings()
        delay = s.reconnect_min_seconds
        while True:
            try:
                await self._session()
                delay = s.reconnect_min_seconds  # 정상 종료 시 백오프 리셋
            except Exception as e:  # noqa: BLE001
                log.warning("hub 연결 오류: %s — %.1fs 후 재연결", e, delay)
            jitter = random.uniform(0, delay * 0.3)
            await asyncio.sleep(delay + jitter)
            delay = min(delay * 2, s.reconnect_max_seconds)

    async def _session(self) -> None:
        s = get_settings()
        async with websockets.connect(s.hub_url, ping_interval=20, ping_timeout=20) as ws:
            hello = {
                "type": "hello",
                "cluster_id": s.cluster_id,
                "agent_version": s.agent_version,
                "auth_token": s.cluster_token,
                "last_command_seq": self._last_command_seq,
                "metrics_available": self.kube.metrics_available,
            }
            await ws.send(json.dumps(hello))
            ack = json.loads(await ws.recv())
            log.info("hub 접속 성공: %s", ack.get("message"))

            await asyncio.gather(self._sender(ws), self._receiver(ws))

    async def _sender(self, ws) -> None:
        s = get_settings()
        while True:
            self._generation += 1
            snap = collect_inventory(self.kube, s.cluster_id, self._generation)
            snap["metrics_available"] = self.kube.metrics_available
            snap["type"] = "telemetry"
            await ws.send(json.dumps(snap))
            _touch(s.heartbeat_file)  # liveness 하트비트
            log.debug("telemetry 전송 gen=%s pods=%s", self._generation, len(snap.get("pods", [])))
            await asyncio.sleep(s.telemetry_interval_seconds)

    async def _receiver(self, ws) -> None:
        async for raw in ws:
            frame = json.loads(raw)
            ftype = frame.get("type")
            if ftype == "command":
                cmd = frame.get("command", {})
                self._last_command_seq = max(self._last_command_seq, cmd.get("command_seq", 0))
                log.info("명령 수신: action=%s %s/%s in %s",
                         cmd.get("type"), cmd.get("target_kind"),
                         cmd.get("target_name"), cmd.get("namespace"))
                result = self.actuator.handle(cmd)
                await ws.send(json.dumps(result))
            elif ftype == "ack":
                pass
            elif ftype == "config":
                log.info("클러스터 설정 수신: %s", frame)
            else:
                log.debug("알 수 없는 프레임: %s", ftype)
