from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeliveryUncertain, DeveloperSlot, MediaKind, SessionStatus
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.media_store import MediaStore
from tg_v_chat.workers.runner import WorkerRunner
from tg_v_chat.workers.session_health import SessionHealthWorker


class Verifier:
    def __init__(self, results):
        self.results = list(results)

    def verify(self, _slot, _session_string):
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _session_factory():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    return factory


def _account_with_sessions(factory, cipher):
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(1001)
        account = uow.accounts.create(user.id, "+15550000001")
        active = uow.sessions.create(
            account.id,
            slot=DeveloperSlot.PRIMARY,
            encrypted_session=cipher.encrypt("primary"),
            status=SessionStatus.ACTIVE,
        )
        standby = uow.sessions.create(
            account.id,
            slot=DeveloperSlot.STANDBY_1,
            encrypted_session=cipher.encrypt("standby"),
            status=SessionStatus.STANDBY,
        )
        uow.accounts.mark_active(account.id)
        uow.commit()
        return account.id, active.id, standby.id


def test_worker_run_forever_invokes_real_cycles_until_stop():
    calls = []
    runner = WorkerRunner([lambda: calls.append("cycle")])

    runner.run_forever(should_stop=lambda: len(calls) >= 2, sleep=lambda _seconds: None)

    assert calls == ["cycle", "cycle"]


def test_session_health_revokes_unauthorized_and_recomputes_account():
    cipher = SessionCipher("test-key")
    factory = _session_factory()
    account_id, active_id, standby_id = _account_with_sessions(factory, cipher)
    worker = SessionHealthWorker(factory, cipher, Verifier([False, True]))

    worker.run_once()

    with UnitOfWork(factory) as uow:
        active = uow.sessions.get(active_id)
        standby = uow.sessions.get(standby_id)
        account = uow.accounts.get(account_id)
        assert active.status == SessionStatus.REVOKED.value
        assert standby.status == SessionStatus.STANDBY.value
        assert account.status == "degraded"


def test_transport_probe_failure_does_not_poison_authorization():
    cipher = SessionCipher("test-key")
    factory = _session_factory()
    account_id, active_id, standby_id = _account_with_sessions(factory, cipher)
    error = DeliveryUncertain("transport_unknown", "network down")
    worker = SessionHealthWorker(factory, cipher, Verifier([error, True]))

    worker.run_once()

    with UnitOfWork(factory) as uow:
        active = uow.sessions.get(active_id)
        standby = uow.sessions.get(standby_id)
        account = uow.accounts.get(account_id)
        assert active.status == SessionStatus.ACTIVE.value
        assert standby.status == SessionStatus.STANDBY.value
        assert account.status == "active"


def test_session_health_worker_retries_terminal_media_release(tmp_path):
    cipher = SessionCipher("test-key")
    factory = _session_factory()
    store = MediaStore(tmp_path)
    artifact = store.write_bytes(b"photo", file_name="a.jpg", mime_type="image/jpeg", media_kind=MediaKind.PHOTO)
    with UnitOfWork(factory) as uow:
        uow.media_artifacts.create(
            direction="incoming",
            storage_key=artifact.storage_key,
            file_name=artifact.file_name,
            mime_type=artifact.mime_type,
            byte_size=artifact.byte_size,
            media_kind=artifact.media_kind,
            sequence=artifact.sequence,
        )
        uow.media_artifacts.mark_ready(artifact.storage_key)
        uow.media_artifacts.mark_sent(artifact.storage_key)
        uow.commit()

    SessionHealthWorker(factory, cipher, Verifier([]), media_store=store).run_once()

    with UnitOfWork(factory) as uow:
        row = uow.media_artifacts.get_by_storage_key(artifact.storage_key)
        assert row.status == "released"
    assert not store.path_for(artifact).exists()
