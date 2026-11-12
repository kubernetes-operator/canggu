"""hello 는 첫 수집 후 전송되어 metrics_available 이 실제 값을 반영한다."""

import asyncio
import json

import app.stream as stream


class _Stop(Exception):
    pass


class _FakeKube:
    def __init__(self) -> None:
        self.metrics_available = False  # 실제 KubeClient 처럼 수집 전엔 False


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg["type"] == "telemetry":
            raise _Stop

    async def recv(self) -> str:
        return json.dumps({"type": "ack", "message": "ok"})


def test_hello_after_first_collect(monkeypatch):
    kube = _FakeKube()
    calls = []

    def fake_collect(k, cluster_id, gen):
        calls.append(gen)
        k.metrics_available = True
        return {"cluster_id": cluster_id, "generation": gen, "pods": []}

    ws = _FakeWS()
    monkeypatch.setattr(stream, "collect_inventory", fake_collect)
    monkeypatch.setattr(stream.websockets, "connect", lambda *a, **kw: ws)

    link = stream.HubLink(kube, actuator=None)

    async def no_recv(_ws):
        return None

    monkeypatch.setattr(link, "_receiver", no_recv)
    try:
        asyncio.run(link._session())
    except _Stop:
        pass

    hello, telem = ws.sent[0], ws.sent[1]
    assert hello["type"] == "hello" and hello["metrics_available"] is True
    # 첫 수집 스냅샷을 그대로 첫 telemetry 로 재사용 (중복 수집 없음)
    assert telem["generation"] == 1 and calls == [1]
