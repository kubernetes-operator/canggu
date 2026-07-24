from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """agent 설정. 환경변수 CANGGU_* 로 오버라이드."""

    model_config = SettingsConfigDict(env_prefix="CANGGU_", env_file=".env", extra="ignore")

    hub_url: str = "ws://localhost:8000/agentlink/ws"
    cluster_id: str = "demo"
    cluster_token: str = "dev-token"     # hub 부트스트랩 토큰 (운영은 mTLS/단기토큰)
    agent_version: str = "0.0.1"

    # 클러스터 없이 합성 데이터로 동작 (개발/데모). 실제 자격증명 불필요.
    mock: bool = False
    # 실 클러스터 모드에서 in-cluster SA 대신 로컬 kubeconfig 사용 여부.
    use_local_kubeconfig: bool = False

    # 발급 kubeconfig 에 들어갈 외부 API 서버 URL. 비어있으면 in-cluster 값 사용.
    kubeconfig_server: str = ""
    kubeconfig_default_ttl: int = 3600
    velero_namespace: str = "velero"

    # Prometheus 기반 p95(설정 시 metrics-server 순간값 대신 사용). 비어있으면 metrics-server.
    prometheus_url: str = ""
    prometheus_window: str = "1h"
    prometheus_quantile: float = 0.95
    prometheus_cache_seconds: float = 60.0

    telemetry_interval_seconds: float = 10.0
    heartbeat_file: str = "/tmp/canggu-agent-alive"  # liveness probe 용 (전송마다 갱신)
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
