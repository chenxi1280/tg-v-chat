from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tg_v_chat.storage.models import Base


POSTGRESQL_PREFIXES = ("postgresql://", "postgresql+psycopg://")


def create_session_factory(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(session_factory) -> None:
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)


def require_postgresql_url(database_url: str) -> None:
    if not database_url.startswith(POSTGRESQL_PREFIXES):
        raise ValueError("runtime database must be PostgreSQL; SQLite is only allowed in isolated tests")
