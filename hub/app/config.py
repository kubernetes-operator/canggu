from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """hub 설정. 환경변수 CANGGU_* 로 오버라이드. 자격증명/시크릿은 커밋 금지."""

    model_config = SettingsConfigDict(env_prefix="CANGGU_", env_file=".env", extra="ignore")

    # 기본은 zero-dependency 로컬 실행을 위한 sqlite. docker-compose 는 postgres 주입.
    database_url: str = "sqlite:///./canggu.db"

    # agent 부트스트랩 토큰(데모용). 운영에서는 클러스터별 발급 + mTLS 로 대체.
    agent_shared_token: str = "dev-token"

    # 자동조정 엔진 기본값
    rightsize_factor: float = 1.5           # usage_p95 * factor > requests 이면 우측정렬
    remediation_default_frozen: bool = False

    cors_origins: str = "http://localhost:5173"

    # 빌드된 웹 SPA(dist) 경로. 비어있으면 정적 서빙 비활성(로컬 dev 는 vite 사용).
    # 컨테이너에서는 /app/web_dist 로 설정.
    web_dist: str = ""

    # 인증(Phase 3). 시크릿은 커밋 금지 — 배포 시 Secret 으로 주입.
    jwt_secret: str = "dev-insecure-secret-change-me"
    session_ttl_seconds: int = 8 * 3600
    # 부트스트랩 admin. 최초 기동 시 DB 에 없으면 생성.
    admin_user: str = "admin"
    admin_password: str = "admin"  # 운영은 Secret 주입(랜덤). dev 기본값.
    # true 면 인증 미들웨어 활성(운영). dev 로컬은 false 로 열어둘 수 있음.
    auth_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
