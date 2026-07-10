from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

HEARTBEAT_MAX_AGE_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 20


class RoleHeartbeat:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def beat(self, role: str) -> None:
        self.path_for(role).write_text(datetime.now(timezone.utc).isoformat())

    def remove(self, role: str) -> None:
        self.path_for(role).unlink(missing_ok=True)

    def path_for(self, role: str) -> Path:
        if role not in {"bot", "listener", "worker"}:
            raise ValueError(f"invalid runtime role: {role}")
        return self.root / f"{role}.heartbeat"


def heartbeat_age_seconds(path: Path) -> float:
    timestamp = datetime.fromisoformat(path.read_text().strip())
    return (datetime.now(timezone.utc) - timestamp).total_seconds()


async def heartbeat_loop(heartbeat: RoleHeartbeat, role: str, sleep) -> None:
    while True:
        heartbeat.beat(role)
        await sleep(HEARTBEAT_INTERVAL_SECONDS)
