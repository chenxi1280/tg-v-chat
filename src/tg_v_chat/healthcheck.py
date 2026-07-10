from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

from tg_v_chat.config import load_config
from tg_v_chat.runtime_health import HEARTBEAT_MAX_AGE_SECONDS, heartbeat_age_seconds
from tg_v_chat.storage.database import create_session_factory, require_postgresql_url


def check_database(database_url: str | None = None) -> None:
    url = database_url or load_config().database_url
    require_postgresql_url(url)
    session_factory = create_session_factory(url)
    with session_factory() as session:
        session.execute(text("select 1"))


def check_role_heartbeat(role: str, heartbeat_root: str | Path) -> None:
    path = Path(heartbeat_root) / f"{role}.heartbeat"
    if not path.exists():
        raise RuntimeError(f"missing heartbeat: {role}")
    if heartbeat_age_seconds(path) > HEARTBEAT_MAX_AGE_SECONDS:
        raise RuntimeError(f"stale heartbeat: {role}")


def main() -> None:
    args = _parse_args()
    try:
        config = load_config()
        check_database(config.database_url)
        if args.role:
            check_role_heartbeat(args.role, config.heartbeat_root)
    except Exception as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _parse_args():
    parser = argparse.ArgumentParser(description="tg-v-chat healthcheck")
    parser.add_argument("--role", choices=("bot", "listener", "worker"))
    return parser.parse_args()


if __name__ == "__main__":
    main()
