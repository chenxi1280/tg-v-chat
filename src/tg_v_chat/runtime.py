from __future__ import annotations

import argparse
import signal
import time

from tg_v_chat.config import load_config
from tg_v_chat.main import build_runtime
from tg_v_chat.telegram.telethon_clients import DeveloperAppConfig, TelethonAuthenticator, TelethonBotProcess


VALID_ROLES = {"bot", "listener", "worker"}


class StopSignal:
    def __init__(self):
        self.stop = False

    def handle(self, _signum, _frame) -> None:
        self.stop = True


def main() -> None:
    args = parse_args()
    config = load_config()
    runtime = build_runtime(config.database_url, config.session_encryption_key, config.bot_token)
    app_config = _primary_app_config(config)
    bot_process = TelethonBotProcess(app_config, config.bot_token, runtime.bot_router(TelethonAuthenticator(app_config)))
    run_role(args.role, bot_runner=bot_process.run)


def run_role(role: str, *, bot_runner=None, wait=None) -> None:
    wait_runner = wait or wait_forever
    if role == "bot":
        if bot_runner is None:
            raise RuntimeError("bot runner is required")
        bot_runner()
        return
    wait_runner(role)


def parse_args():
    parser = argparse.ArgumentParser(description="tg-v-chat runtime process")
    parser.add_argument("--role", choices=sorted(VALID_ROLES), required=True)
    return parser.parse_args()


def wait_forever(role: str) -> None:
    stop_signal = StopSignal()
    signal.signal(signal.SIGTERM, stop_signal.handle)
    signal.signal(signal.SIGINT, stop_signal.handle)
    while not stop_signal.stop:
        time.sleep(5)
    print(f"tg-v-chat {role} stopped")


def _primary_app_config(config) -> DeveloperAppConfig:
    try:
        api_id = int(config.primary_api_id)
    except ValueError as exc:
        raise RuntimeError("TG_V_CHAT_PRIMARY_API_ID must be an integer") from exc
    return DeveloperAppConfig(api_id, config.primary_api_hash)


if __name__ == "__main__":
    main()
