from __future__ import annotations

import sys

from sqlalchemy import text

from tg_v_chat.config import load_config
from tg_v_chat.storage.database import create_session_factory, require_postgresql_url


def check_database() -> None:
    config = load_config()
    require_postgresql_url(config.database_url)
    session_factory = create_session_factory(config.database_url)
    with session_factory() as session:
        session.execute(text("select 1"))


def main() -> None:
    try:
        check_database()
    except Exception as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
