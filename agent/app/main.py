"""canggu agent 진입점."""

from __future__ import annotations

import asyncio
import logging

from app.actuators.remediation import Actuator
from app.config import get_settings
from app.kube import KubeClient
from app.stream import HubLink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("canggu.agent")


async def _main() -> None:
    s = get_settings()
    log.info("agent 시작: cluster=%s hub=%s mock=%s", s.cluster_id, s.hub_url, s.mock)
    kube = KubeClient()
    actuator = Actuator(kube)
    link = HubLink(kube, actuator)
    await link.run_forever()


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("agent 종료")


if __name__ == "__main__":
    main()
