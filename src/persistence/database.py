"""Database engine and session management."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.common.config import get_settings
from src.common.logging import get_logger

log = get_logger(__name__)


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        is_sqlite = url.startswith("sqlite")
        if is_sqlite:
            # SQLite: use StaticPool so in-process tests share the same connection,
            # and connect_args to allow multi-threaded access.
            from sqlalchemy.pool import StaticPool
            _engine = create_engine(
                url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
                echo=settings.database_echo,
            )
        else:
            _engine = create_engine(
                url,
                pool_size=settings.database_pool_size,
                echo=settings.database_echo,
                pool_pre_ping=True,
            )
        log.info("database.engine_created", url=url.split("@")[-1] if "@" in url else url)
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


@contextmanager
def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all_tables() -> None:
    from src.persistence import models as _  # noqa: F401 – ensures models are registered
    Base.metadata.create_all(bind=get_engine())
    log.info("database.tables_created")


def drop_all_tables() -> None:
    from src.persistence import models as _  # noqa: F401
    Base.metadata.drop_all(bind=get_engine())
    log.warning("database.tables_dropped")
