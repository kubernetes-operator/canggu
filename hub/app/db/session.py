from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

# sqlite 는 스레드 공유 허용 필요 (uvicorn/anyio 워커).
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}

engine = create_engine(_settings.database_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Phase 0: 메타데이터로 테이블 생성. Phase 1+ 에서 Alembic 마이그레이션으로 전환."""
    from app.db import models  # noqa: F401  (모델 등록)

    models.Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """FastAPI 의존성."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
