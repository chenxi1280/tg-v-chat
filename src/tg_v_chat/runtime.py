from __future__ import annotations

import argparse
import signal
import time

from tg_v_chat.config import load_config
from tg_v_chat.main import build_runtime


VALID_ROLES = {"bot", "listener", "worker"}


class StopSignal:
    def __init__(self):
        self.stop = False

    def handle(self, _signum, _frame) -> None:
        self.stop = True


def main() -> None:
    args = parse_args()
    config = load_config()
    build_runtime(config.database_url, config.session_encryption_key, config.bot_token)
    wait_forever(args.role)


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


if __name__ == "__main__":
    main()
