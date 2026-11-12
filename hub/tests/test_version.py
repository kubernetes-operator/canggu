"""hub 버전은 설정(CANGGU_HUB_VERSION)에서 오며 /healthz 로 노출된다."""

import asyncio

from app import main
from app.config import get_settings


def test_healthz_reports_configured_version():
    body = asyncio.run(main.healthz())
    assert body == {"status": "ok", "version": get_settings().hub_version}
    assert main.app.version == get_settings().hub_version
