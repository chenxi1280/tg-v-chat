from datetime import datetime, timedelta, timezone

import pytest

from tg_v_chat.healthcheck import check_role_heartbeat
from tg_v_chat.runtime import run_role
from tg_v_chat.runtime_health import HEARTBEAT_MAX_AGE_SECONDS, RoleHeartbeat


def test_failed_heartbeat_write_preserves_last_complete_timestamp(tmp_path, monkeypatch):
    heartbeat = RoleHeartbeat(tmp_path)
    heartbeat.beat("bot")
    path = heartbeat.path_for("bot")
    previous = path.read_text()
    write_text = type(path).write_text

    def disk_full(target, data):
        write_text(target, "")
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(type(path), "write_text", disk_full)
        with pytest.raises(OSError, match="No space left on device"):
            heartbeat.beat("bot")

    assert path.read_text() == previous
    check_role_heartbeat("bot", tmp_path)
    heartbeat.beat("bot")
    check_role_heartbeat("bot", tmp_path)
    assert list(tmp_path.iterdir()) == [path]


def test_worker_sleep_uses_one_clock_read_per_deadline_check(tmp_path, monkeypatch):
    from tg_v_chat import runtime

    times = iter([0, 0.5, 1.5])
    sleeps = []

    def sleep(seconds):
        if seconds < 0:
            raise ValueError("sleep length must be non-negative")
        sleeps.append(seconds)

    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(runtime.time, "sleep", sleep)

    runtime._sleep_with_heartbeat(1, RoleHeartbeat(tmp_path), runtime.StopSignal())

    assert sleeps == [0.5]
    check_role_heartbeat("worker", tmp_path)


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
