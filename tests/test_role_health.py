from datetime import datetime, timedelta, timezone

import pytest

from tg_v_chat.healthcheck import check_role_heartbeat
from tg_v_chat.runtime import run_role
from tg_v_chat.runtime_health import HEARTBEAT_MAX_AGE_SECONDS, RoleHeartbeat


def test_role_heartbeat_accepts_fresh_and_rejects_stale(tmp_path):
    heartbeat = RoleHeartbeat(tmp_path)

    heartbeat.beat("bot")
    check_role_heartbeat("bot", tmp_path)

    stale = datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_MAX_AGE_SECONDS + 1)
    (tmp_path / "bot.heartbeat").write_text(stale.isoformat())
    with pytest.raises(RuntimeError, match="stale heartbeat"):
        check_role_heartbeat("bot", tmp_path)


def test_role_heartbeat_rejects_missing_file(tmp_path):
    with pytest.raises(RuntimeError, match="missing heartbeat"):
        check_role_heartbeat("listener", tmp_path)


def test_worker_role_runs_worker_runner_instead_of_idle_wait():
    calls = []

    run_role(
        "worker",
        worker_runner=lambda: calls.append("worker"),
        wait=lambda _role: calls.append("wait"),
    )

    assert calls == ["worker"]
