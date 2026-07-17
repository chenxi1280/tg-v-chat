from __future__ import annotations

import pytest

from account_management_helpers import FakeAuthenticator, submit_code_with_keypad
from tg_v_chat.bot.account_management import AccountManagementService
from tg_v_chat.bot.account_management.parsing import parse_slot_action
from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotUpdateRouter
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeveloperSlot, IncomingPrivateMessage, MediaKind, SessionStatus
from tg_v_chat.services.auth import AuthChallenge, AuthService, AuthStep, AuthenticatedSession
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork
from tg_v_chat.telegram.telethon_clients.authenticator import SlotAuthenticatorRegistry
from tg_v_chat.telegram.telethon_clients.config import DeveloperAppConfig


class SlotSessionAuthenticator(FakeAuthenticator):
    def complete_code(self, challenge, code):
        self.codes.append(code)
        return AuthenticatedSession(f"session-{challenge.developer_slot.value}", 7001, "账号", "owner")


class RecordingDelegate:
    def __init__(self, config):
        self.config = config
        self.calls = []

    def start(self, phone_number, slot):
        self.calls.append(("start", slot))
        return AuthChallenge(phone_number, slot, "hash")

    def complete_code(self, challenge, code):
        self.calls.append(("code", challenge.developer_slot))
        return AuthenticatedSession(str(self.config.api_id), 7001, None, None)


class CancellingCodeAuthenticator(FakeAuthenticator):
    def __init__(self, factory):
        super().__init__()
        self._factory = factory

    def complete_code(self, challenge, code):
        self.codes.append(code)
        with UnitOfWork(self._factory) as uow:
            uow.auth_challenges.mark_status(challenge.id, "cancelled")
            uow.commit()
        return AuthenticatedSession("late-session", 7001, self.display_name, self.username)

    def complete_password(self, challenge, password):
        self.calls.append(("password", challenge.developer_slot))
        return AuthenticatedSession(str(self.config.api_id), 7001, None, None)


def _build_router(authenticator=None):
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    authenticator = authenticator or SlotSessionAuthenticator()
    cipher = SessionCipher("test-key")
    service = AccountManagementService(factory, authenticator, cipher)
    return BotUpdateRouter(lambda _command: None, service), authenticator, factory, cipher


def _active_account(factory, cipher):
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(146517)
        account = uow.accounts.create(user.id, "+15550000001")
        uow.sessions.create(
            account.id,
            slot=DeveloperSlot.PRIMARY,
            encrypted_session=cipher.encrypt("session-primary-old"),
            status=SessionStatus.ACTIVE,
        )
        for slot in (DeveloperSlot.STANDBY_1, DeveloperSlot.STANDBY_2):
            uow.sessions.create(account.id, slot=slot, encrypted_session=None, status=SessionStatus.EXPIRED)
        uow.accounts.mark_active(account.id)
        uow.commit()
        return account.id


def _complete_slot(router, account_id, slot, *, action="bind"):
    start = router.handle_callback(
        BotCallback(146517, f"account.slot.{action}:{account_id}:{slot.value}")
    )[0]
    return submit_code_with_keypad(router, start)


def _fill_code_prompt(router, prompt, *, code="12345"):
    response = prompt
    for digit in code:
        button = next(item for item in response.buttons if item.text == digit)
        response = router.handle_callback(BotCallback(146517, button.data))[0]
    return response


def test_initial_bind_writes_primary_session_and_empty_placeholders_only():
    router, _authenticator, factory, cipher = _build_router()
    service = router._account_management

    service.start_bind(146517)
    phone_prompt = service.handle_text(146517, "+15550000001")
    submit_code_with_keypad(router, phone_prompt)

    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        account = uow.accounts.list_for_user(user.id)[0]
        slots = {row.developer_slot: row for row in uow.sessions.list_for_account(account.id)}
        assert cipher.decrypt(slots["primary"].encrypted_session) == "session-primary"
        assert slots["primary"].status == "active"
        assert slots["standby_1"].encrypted_session is None
        assert slots["standby_2"].encrypted_session is None


def test_authenticator_registry_routes_each_challenge_to_its_slot_app_config():
    delegates = {}

    def factory(config):
        delegate = RecordingDelegate(config)
        delegates[config.api_id] = delegate
        return delegate

    registry = SlotAuthenticatorRegistry(
        {
            DeveloperSlot.PRIMARY: DeveloperAppConfig(1, "p"),
            DeveloperSlot.STANDBY_1: DeveloperAppConfig(2, "s1"),
            DeveloperSlot.STANDBY_2: DeveloperAppConfig(3, "s2"),
        },
        authenticator_factory=factory,
    )

    for slot, api_id in (
        (DeveloperSlot.PRIMARY, 1),
        (DeveloperSlot.STANDBY_1, 2),
        (DeveloperSlot.STANDBY_2, 3),
    ):
        challenge = registry.start("+15550000001", slot)
        assert registry.complete_code(challenge, "12345").session_string == str(api_id)
        assert delegates[api_id].calls == [("start", slot), ("code", slot)]


def test_all_three_slots_store_distinct_session_strings():
    router, _authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)

    _complete_slot(router, account_id, DeveloperSlot.PRIMARY, action="reauth")
    _complete_slot(router, account_id, DeveloperSlot.STANDBY_1)
    _complete_slot(router, account_id, DeveloperSlot.STANDBY_2)

    with UnitOfWork(factory) as uow:
        sessions = uow.sessions.list_for_account(account_id)
        plaintext = {cipher.decrypt(row.encrypted_session) for row in sessions}
        assert plaintext == {"session-primary", "session-standby_1", "session-standby_2"}


@pytest.mark.parametrize("slot", [DeveloperSlot.STANDBY_1, DeveloperSlot.STANDBY_2])
def test_slot_bind_targets_only_requested_slot_and_preserves_other_sessions(slot):
    router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)

    response = _complete_slot(router, account_id, slot)

    assert "授权成功" in response.text
    assert authenticator.started == [("+15550000001", slot)]
    with UnitOfWork(factory) as uow:
        sessions = {row.developer_slot: row for row in uow.sessions.list_for_account(account_id)}
        assert cipher.decrypt(sessions[slot.value].encrypted_session) == f"session-{slot.value}"
        assert sessions[slot.value].status == "standby"
        assert cipher.decrypt(sessions["primary"].encrypted_session) == "session-primary-old"
        untouched = DeveloperSlot.STANDBY_2 if slot is DeveloperSlot.STANDBY_1 else DeveloperSlot.STANDBY_1
        assert sessions[untouched.value].encrypted_session is None


def test_slot_reauthorization_preserves_account_and_relay_history():
    router, _authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        relay = uow.relays.create_or_get(
            IncomingPrivateMessage(account_id, 99, 7, MediaKind.TEXT, "hello", None, 0)
        )[0]
        uow.mappings.create(88, relay, user.id)
        uow.commit()

    _complete_slot(router, account_id, DeveloperSlot.PRIMARY, action="reauth")

    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        assert uow.accounts.list_for_user(user.id)[0].id == account_id
        assert uow.mappings.get_by_bot_message(user.id, 88).status == "active"
        assert uow.session.get(type(relay), relay.id).payload == "hello"


def test_slot_callback_rejects_account_owned_by_another_user():
    router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)

    with pytest.raises(LookupError):
        router.handle_callback(BotCallback(999999, f"account.slot.bind:{account_id}:standby_1"))

    assert authenticator.started == []


def test_duplicate_slot_callback_reuses_one_active_challenge_and_one_code_send():
    router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)
    data = f"account.slot.bind:{account_id}:standby_1"

    first = router.handle_callback(BotCallback(146517, data))[0]
    second = router.handle_callback(BotCallback(146517, data))[0]

    assert "验证码已发送" in first.text
    assert "验证码已发送" in second.text
    assert authenticator.started == [("+15550000001", DeveloperSlot.STANDBY_1)]
    with UnitOfWork(factory) as uow:
        challenges = uow.auth_challenges.list_for_account(account_id)
        active = [row for row in challenges if row.status in {"code_required", "password_required"}]
        assert len(active) == 1


def test_direct_auth_service_reuses_active_slot_challenge():
    _router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)

    with UnitOfWork(factory) as uow:
        auth = AuthService(uow, authenticator, cipher)
        first = auth.start_slot_authorization(
            146517,
            account_id,
            DeveloperSlot.STANDBY_1,
            action="bind",
        )
        second = auth.start_slot_authorization(
            146517,
            account_id,
            DeveloperSlot.STANDBY_1,
            action="bind",
        )

    assert first.id == second.id
    assert authenticator.started == [("+15550000001", DeveloperSlot.STANDBY_1)]


def test_starting_other_slot_cancels_previous_active_wizard_for_user():
    _router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)

    with UnitOfWork(factory) as uow:
        auth = AuthService(uow, authenticator, cipher)
        first = auth.start_slot_authorization(
            146517,
            account_id,
            DeveloperSlot.STANDBY_1,
            action="bind",
        )
        second = auth.start_slot_authorization(
            146517,
            account_id,
            DeveloperSlot.STANDBY_2,
            action="bind",
        )

    assert first.id != second.id
    with UnitOfWork(factory) as uow:
        challenges = {row.id: row.status for row in uow.auth_challenges.list_for_account(account_id)}
        assert challenges[first.id] == "cancelled"
        assert challenges[second.id] == AuthStep.CODE_REQUIRED.value


def test_completed_code_submission_is_idempotent_without_second_telegram_call():
    _router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)

    with UnitOfWork(factory) as uow:
        auth = AuthService(uow, authenticator, cipher)
        challenge = auth.start_slot_authorization(
            146517,
            account_id,
            DeveloperSlot.STANDBY_1,
            action="bind",
        )
        assert auth.submit_code(challenge.id, "11111") is AuthStep.COMPLETE
        assert auth.submit_code(challenge.id, "22222") is AuthStep.COMPLETE

    assert authenticator.codes == ["11111"]


def test_bot_submit_callback_replay_returns_success_without_second_telegram_call():
    router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)
    prompt = router.handle_callback(BotCallback(146517, f"account.slot.bind:{account_id}:standby_1"))[0]
    filled = _fill_code_prompt(router, prompt)
    submit = next(item for item in filled.buttons if item.text == "✅ 提交")

    first = router.handle_callback(BotCallback(146517, submit.data))[0]
    second = router.handle_callback(BotCallback(146517, submit.data))[0]

    assert "授权成功" in first.text
    assert "账号详情" in second.text
    assert authenticator.codes == ["12345"]


def test_cancel_after_completed_challenge_does_not_report_cancelled():
    router, _authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)
    prompt = router.handle_callback(BotCallback(146517, f"account.slot.bind:{account_id}:standby_1"))[0]
    submit_code_with_keypad(router, prompt)

    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        challenge = uow.auth_challenges.list_for_account(account_id)[-1]
        uow.conversation_states.set(user.id, "awaiting_code", challenge.id)
        uow.commit()

    response = router.handle_callback(BotCallback(146517, "account.bind.cancel"))[0]

    assert "已完成" in response.text
    with UnitOfWork(factory) as uow:
        assert uow.auth_challenges.get(challenge.id).status == AuthStep.COMPLETE.value


def test_cancelled_challenge_during_code_completion_does_not_write_session():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    authenticator = CancellingCodeAuthenticator(factory)
    cipher = SessionCipher("test-key")
    account_id = _active_account(factory, cipher)

    with UnitOfWork(factory) as uow:
        target = uow.sessions.get_for_account(account_id, DeveloperSlot.STANDBY_1)
        before = (target.encrypted_session, target.status, target.revision)
        auth = AuthService(uow, authenticator, cipher)
        challenge = auth.start_slot_authorization(
            146517,
            account_id,
            DeveloperSlot.STANDBY_1,
            action="bind",
        )
        with pytest.raises(ValueError, match="cancelled"):
            auth.submit_code(challenge.id, "12345")

    with UnitOfWork(factory) as uow:
        target = uow.sessions.get_for_account(account_id, DeveloperSlot.STANDBY_1)
        assert (target.encrypted_session, target.status, target.revision) == before
        assert uow.auth_challenges.get(challenge.id).status == "cancelled"


@pytest.mark.parametrize(
    ("slot", "action"),
    [(DeveloperSlot.PRIMARY, "bind"), (DeveloperSlot.STANDBY_1, "reauth")],
)
def test_slot_action_must_match_current_authorization(slot, action):
    router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)

    with pytest.raises(ValueError, match="槽位授权动作不匹配"):
        router.handle_callback(BotCallback(146517, f"account.slot.{action}:{account_id}:{slot.value}"))

    assert authenticator.started == []


def test_disabling_account_after_callback_blocks_code_completion_and_session_write():
    router, authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)
    prompt = router.handle_callback(BotCallback(146517, f"account.slot.bind:{account_id}:standby_1"))[0]
    with UnitOfWork(factory) as uow:
        target = uow.sessions.get_for_account(account_id, DeveloperSlot.STANDBY_1)
        before = (target.encrypted_session, target.status, target.revision)
        uow.accounts.mark_disabled(account_id)
        uow.commit()

    with pytest.raises(ValueError, match="账号状态不允许继续授权"):
        submit_code_with_keypad(router, prompt)

    assert authenticator.codes == []
    with UnitOfWork(factory) as uow:
        target = uow.sessions.get_for_account(account_id, DeveloperSlot.STANDBY_1)
        assert (target.encrypted_session, target.status, target.revision) == before


def test_disabling_account_during_2fa_blocks_password_completion_and_session_write():
    router, authenticator, factory, cipher = _build_router(FakeAuthenticator(needs_password=True))
    account_id = _active_account(factory, cipher)
    prompt = router.handle_callback(BotCallback(146517, f"account.slot.bind:{account_id}:standby_1"))[0]
    password_prompt = submit_code_with_keypad(router, prompt)
    with UnitOfWork(factory) as uow:
        target = uow.sessions.get_for_account(account_id, DeveloperSlot.STANDBY_1)
        before = (target.encrypted_session, target.status, target.revision)
        uow.accounts.mark_disabled(account_id)
        uow.commit()

    assert "2FA" in password_prompt.text
    with pytest.raises(ValueError, match="账号状态不允许继续授权"):
        router.handle(BotIncomingMessage(146517, 99, None, "secret"))

    assert authenticator.passwords == []
    with UnitOfWork(factory) as uow:
        target = uow.sessions.get_for_account(account_id, DeveloperSlot.STANDBY_1)
        assert (target.encrypted_session, target.status, target.revision) == before


def test_slot_callbacks_and_detail_buttons_are_explicit_and_short():
    router, _authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)

    detail = router.handle_callback(BotCallback(146517, f"account.detail:{account_id}"))[0]
    callback_data = {button.data for button in detail.buttons}

    assert f"account.slot.reauth:{account_id}:primary" in callback_data
    assert f"account.slot.bind:{account_id}:standby_1" in callback_data
    assert f"account.slot.bind:{account_id}:standby_2" in callback_data
    assert max(map(len, callback_data)) <= 64


@pytest.mark.parametrize(
    "data",
    [
        "account.slot.bind:1:standby_3",
        "account.slot.reauth:not-an-id:primary",
        "account.slot.bind:1:primary:extra",
    ],
)
def test_slot_callback_parser_rejects_malformed_data(data):
    with pytest.raises(ValueError, match="无效槽位授权操作"):
        parse_slot_action(data)


@pytest.mark.parametrize(
    ("primary_status", "standby_status", "expected"),
    [
        (SessionStatus.ACTIVE, SessionStatus.EXPIRED, "active"),
        (SessionStatus.FAILED, SessionStatus.STANDBY, "degraded"),
        (SessionStatus.FAILED, SessionStatus.EXPIRED, "reauth_required"),
    ],
)
def test_slot_authorization_recomputes_account_status(primary_status, standby_status, expected):
    router, _authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)
    with UnitOfWork(factory) as uow:
        primary = uow.sessions.get_for_account(account_id, DeveloperSlot.PRIMARY)
        standby = uow.sessions.get_for_account(account_id, DeveloperSlot.STANDBY_1)
        primary.status = primary_status.value
        standby.status = standby_status.value
        standby.encrypted_session = cipher.encrypt("standby") if standby_status is SessionStatus.STANDBY else None
        uow.accounts.mark_reauth_required(account_id)
        uow.commit()

    with UnitOfWork(factory) as uow:
        from tg_v_chat.services.auth import recompute_account_status

        recompute_account_status(uow, account_id)
        uow.commit()
        assert uow.accounts.get(account_id).status == expected


@pytest.mark.parametrize("protected_status", ["disabled", "deleted"])
def test_slot_status_recompute_never_overwrites_disabled_or_deleted(protected_status):
    router, _authenticator, factory, cipher = _build_router()
    account_id = _active_account(factory, cipher)
    with UnitOfWork(factory) as uow:
        getattr(uow.accounts, f"mark_{protected_status}")(account_id)
        uow.commit()

    with UnitOfWork(factory) as uow:
        from tg_v_chat.services.auth import recompute_account_status

        recompute_account_status(uow, account_id)
        uow.commit()
        assert uow.accounts.get(account_id).status == protected_status
