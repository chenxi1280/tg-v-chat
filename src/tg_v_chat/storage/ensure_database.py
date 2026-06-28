from __future__ import annotations

import os

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from tg_v_chat.storage.database import require_postgresql_url


MAINTENANCE_DATABASE = "postgres"


def database_name(database_url: str) -> str:
    require_postgresql_url(database_url)
    name = make_url(database_url).database
    if not name:
        raise ValueError("database URL must include a database name")
    return name


def maintenance_database_url(database_url: str) -> str:
    require_postgresql_url(database_url)
    return make_url(database_url).set(database=MAINTENANCE_DATABASE).render_as_string(hide_password=False)


def psycopg_connection_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return database_url


def ensure_database(database_url: str) -> None:
    target_database = database_name(database_url)
    maintenance_url = psycopg_connection_url(maintenance_database_url(database_url))

    with psycopg.connect(maintenance_url) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_database,))
            if cursor.fetchone():
                return
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_database)))


def main() -> None:
    database_url = os.getenv("TG_V_CHAT_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TG_V_CHAT_DATABASE_URL is required")
    ensure_database(database_url)


if __name__ == "__main__":
    main()
