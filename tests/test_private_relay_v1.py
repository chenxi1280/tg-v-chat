from datetime import datetime, timezone

import pytest

from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import (
    DeliveryFailure,
    DeveloperSlot,
    IncomingPrivateMessage,
    MediaKind,
    OutgoingReply,
    SessionFailure,
    SessionStatus,
)
from tg_v_chat.services.auth import AuthChallenge, AuthService, AuthStep, AuthenticatedSession, PasswordRequired
from tg_v_chat.services.relay import PrivateRelayService
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.models import BotPushMessageModel, RelayMessageModel, ReplyMappingModel
from tg_v_chat.storage.repositories import UnitOfWork


class FakeAuthenticator:
    def __init__(self, needs_password=False):
        self.needs_password = needs_password
        self.password_checked = False
        self.code_challenge_pending_session = None

    def start(self, phone_number, slot):
        return AuthChallenge(phone_number, slot, "phone_code_hash", pending_session="pending-session")

    def complete_code(self, challenge, code):
        self.code_challenge_pending_session = challenge.pending_session
        if self.needs_password:
            return PasswordRequired("partial-session-after-code")
        return AuthenticatedSession("session-string", 7001, "小号A", "example_user")

    def complete_password(self, challenge, password):
        self.password_checked = True
        return AuthenticatedSession("session-string-2fa", 7001, "小号A", "example_user")


class PasswordRequiredAuthenticator(FakeAuthenticator):
    def __init__(self):
        super().__init__(needs_password=True)
        self.password_challenge_pending_session = None

    def complete_code(self, challenge, code):
        self.code_challenge_pending_session = challenge.pending_session
        return PasswordRequired("partial-session-after-code")

    def complete_password(self, challenge, password):
        self.password_challenge_pending_session = challenge.pending_session
        return AuthenticatedSession("session-string-2fa", 7001, "小号A", "example_user")


class FakeBotGateway:
    def __init__(self):
        self.next_message_id = 500
        self.pushes = []
        self.fail_next_media = False

    def push_private_message(self, system_user_id, message):
        if self.fail_next_media:
            raise RuntimeError("media download failed")
        self.next_message_id += 1
        self.pushes.append((system_user_id, message))
        return self.next_message_id

    def notify_failure(self, system_user_id, text):
        self.pushes.append((system_user_id, text))


class FakeSenderPool:
    def __init__(self, failing_slots=None):
        self.failing_slots = set(failing_slots or [])
        self.sent = []

    def send_reply(self, session_slot, peer, reply):
        if session_slot.developer_slot in self.failing_slots:
            raise SessionFailure(f"{session_slot.developer_slot.value} failed")
        self.sent.append((session_slot.developer_slot, peer, reply))
        return 9000 + len(self.sent)


@pytest.fixture()
def session_factory():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    return factory


@pytest.fixture()
def uow(session_factory):
    factory = session_factory
    with UnitOfWork(factory) as unit:
        yield unit


def create_active_account(uow, system_user_id=1):
    user = uow.users.get_or_create(system_user_id)
    account = uow.accounts.create(user.id, "+15550000001")
    for slot in DeveloperSlot:
        status = SessionStatus.ACTIVE if slot is DeveloperSlot.PRIMARY else SessionStatus.STANDBY
        uow.sessions.create(account.id, slot=slot, encrypted_session="encrypted", status=status)
    uow.accounts.mark_active(account.id)
    uow.commit()
    return account


def test_private_push_uses_telegram_user_id_and_stores_internal_owner(uow):
    telegram_user_id = 7_677_366_761
    account = create_active_account(uow, system_user_id=telegram_user_id)
    bot = FakeBotGateway()
    relay = PrivateRelayService(uow, bot, FakeSenderPool())

    pushed = relay.receive_private_message(
        IncomingPrivateMessage(account.id, 88, 101, MediaKind.TEXT, "hi", None, 0)
    )
    push = uow.pushes.get_by_relay(pushed.relay_message_id)
    mapping = uow.mappings.get_by_bot_message(account.system_user_id, pushed.bot_message_id)

    assert bot.pushes[0][0] == telegram_user_id
    assert push.system_user_id == account.system_user_id
    assert mapping.system_user_id == account.system_user_id


def test_relay_persists_display_metadata(uow):
    sent_at = datetime(2026, 6, 28, 15, 37, 6, tzinfo=timezone.utc)
    account = create_active_account(uow, system_user_id=7_677_366_761)
    relay = PrivateRelayService(uow, FakeBotGateway(), FakeSenderPool())

    result = relay.receive_private_message(
        IncomingPrivateMessage(
            account.id,
            88,
            102,
            MediaKind.TEXT,
            "12131",
            None,
            0,
            sender_name="洋芋",
            sent_at=sent_at,
        )
    )
    row = uow.session.get(RelayMessageModel, result.relay_message_id)
    pushed = uow.mappings.get_by_bot_message(account.system_user_id, result.bot_message_id)

    assert row.sender_name == "洋芋"
    assert row.sent_at.replace(tzinfo=timezone.utc) == sent_at
    assert pushed.peer_id == 88


def test_relay_pushes_recipient_account_name_and_username(uow):
    account = create_active_account(uow, system_user_id=7_677_366_761)
    uow.accounts.update_profile(account.id, display_name="小号A", username="example_user")
    bot = FakeBotGateway()
    relay = PrivateRelayService(uow, bot, FakeSenderPool())

    relay.receive_private_message(
        IncomingPrivateMessage(account.id, 88, 103, MediaKind.TEXT, "hello", None, 0)
    )

    pushed = bot.pushes[0][1]
    assert pushed.recipient_account_name == "小号A"
    assert pushed.recipient_username == "example_user"


def test_binding_rejects_twenty_first_account(uow):
    auth = AuthService(uow, FakeAuthenticator(), SessionCipher("test-key"))
    for index in range(20):
        auth.start_binding(1, f"+15550000{index:02d}", DeveloperSlot.PRIMARY)

    with pytest.raises(ValueError, match="最多绑定 20 个"):
        auth.start_binding(1, "+15559999999", DeveloperSlot.PRIMARY)


def test_phone_code_and_2fa_binding_persist_encrypted_sessions(uow):
    auth = AuthService(uow, FakeAuthenticator(needs_password=True), SessionCipher("test-key"))
    challenge = auth.start_binding(1, "+15550000001", DeveloperSlot.PRIMARY)

    assert auth.submit_code(challenge.id, "12345") is AuthStep.PASSWORD_REQUIRED
    account = auth.submit_password(challenge.id, "secret")

    slots = uow.sessions.list_for_account(account.id)
    assert account.status == "active"
    assert len(slots) == 3
    assert slots[0].encrypted_session != "session-string-2fa"


def test_binding_preserves_pending_login_session_encrypted(uow):
    authenticator = FakeAuthenticator()
    cipher = SessionCipher("test-key")
    auth = AuthService(uow, authenticator, cipher)

    challenge = auth.start_binding(1, "+15550000001", DeveloperSlot.PRIMARY)
    model = uow.auth_challenges.get(challenge.id)

    assert model.pending_session != "pending-session"
    assert cipher.decrypt(model.pending_session) == "pending-session"
    assert auth.submit_code(challenge.id, "12345") is AuthStep.COMPLETE
    assert authenticator.code_challenge_pending_session == "pending-session"


def test_2fa_password_uses_persisted_partial_session(uow):
    authenticator = PasswordRequiredAuthenticator()
    cipher = SessionCipher("test-key")
    auth = AuthService(uow, authenticator, cipher)
    challenge = auth.start_binding(1, "+15550000001", DeveloperSlot.PRIMARY)

    assert auth.submit_code(challenge.id, "12345") is AuthStep.PASSWORD_REQUIRED
    model = uow.auth_challenges.get(challenge.id)
    assert cipher.decrypt(model.pending_session) == "partial-session-after-code"
    auth.submit_password(challenge.id, "secret")

    assert authenticator.password_challenge_pending_session == "partial-session-after-code"


def test_incoming_private_message_is_idempotent_and_album_ordered(uow):
    account = create_active_account(uow)
    bot = FakeBotGateway()
    relay = PrivateRelayService(uow, bot, FakeSenderPool())
    album = [
        IncomingPrivateMessage(account.id, 88, 12, MediaKind.PHOTO, "second", "g1", 2),
        IncomingPrivateMessage(account.id, 88, 11, MediaKind.PHOTO, "first", "g1", 1),
    ]

    pending = relay.receive_private_message(album[0])
    duplicate = relay.receive_private_message(album[0])
    first = relay.receive_private_message(album[1])

    assert pending.bot_message_id is None
    assert duplicate.bot_message_id is None
    assert first.bot_message_id is not None
    assert [item[1].sequence for item in bot.pushes] == [1, 2]
    assert relay.ordered_media_group(account.id, "g1") == [first.relay_message_id, pending.relay_message_id]


def test_reply_uses_mapping_and_fails_explicitly_without_reply(uow):
    account = create_active_account(uow)
    relay = PrivateRelayService(uow, FakeBotGateway(), FakeSenderPool())

    with pytest.raises(ValueError, match="必须 reply"):
        relay.handle_bot_reply(OutgoingReply(1, 700, None, MediaKind.TEXT, "hello"))

    with pytest.raises(LookupError, match="ReplyMapping 不存在"):
        relay.handle_bot_reply(OutgoingReply(1, 701, 12345, MediaKind.TEXT, "hello"))


def test_reply_passes_peer_access_hash_to_sender(uow):
    telegram_user_id = 7_677_366_761
    account = create_active_account(uow, system_user_id=telegram_user_id)
    senders = FakeSenderPool()
    relay = PrivateRelayService(uow, FakeBotGateway(), senders)
    pushed = relay.receive_private_message(
        IncomingPrivateMessage(account.id, 88, 101, MediaKind.TEXT, "hi", None, 0, 123456789)
    )

    relay.handle_bot_reply(OutgoingReply(telegram_user_id, 704, pushed.bot_message_id, MediaKind.TEXT, "owner"))

    assert senders.sent[0][1].id == 88
    assert senders.sent[0][1].access_hash == 123456789


def test_reply_rejects_cross_user_mapping_without_sending(uow):
    telegram_user_id = 7_677_366_761
    account = create_active_account(uow, system_user_id=telegram_user_id)
    senders = FakeSenderPool()
    relay = PrivateRelayService(uow, FakeBotGateway(), senders)
    pushed = relay.receive_private_message(
        IncomingPrivateMessage(account.id, 88, 101, MediaKind.TEXT, "hi", None, 0)
    )
    uow.users.get_or_create(2)

    with pytest.raises(LookupError, match="ReplyMapping 不存在"):
        relay.handle_bot_reply(OutgoingReply(2, 703, pushed.bot_message_id, MediaKind.TEXT, "cross-user"))

    assert senders.sent == []

    relay.handle_bot_reply(OutgoingReply(telegram_user_id, 704, pushed.bot_message_id, MediaKind.TEXT, "owner"))
    with pytest.raises(LookupError, match="ReplyMapping 不存在"):
        relay.handle_bot_reply(OutgoingReply(2, 704, pushed.bot_message_id, MediaKind.TEXT, "reply-id-collision"))


def test_bot_message_ids_are_scoped_per_system_user(uow):
    class PerChatBotGateway(FakeBotGateway):
        def push_private_message(self, system_user_id, message):
            self.pushes.append((system_user_id, message))
            return 1

    first_account = create_active_account(uow, system_user_id=1001)
    second_account = create_active_account(uow, system_user_id=1002)
    relay = PrivateRelayService(uow, PerChatBotGateway(), FakeSenderPool())

    first = relay.receive_private_message(
        IncomingPrivateMessage(first_account.id, 88, 101, MediaKind.TEXT, "first", None, 0)
    )
    second = relay.receive_private_message(
        IncomingPrivateMessage(second_account.id, 99, 102, MediaKind.TEXT, "second", None, 0)
    )
    first_reply = OutgoingReply(1001, 1, first.bot_message_id, MediaKind.TEXT, "reply one")
    second_reply = OutgoingReply(1002, 1, second.bot_message_id, MediaKind.TEXT, "reply two")

    assert first.bot_message_id == second.bot_message_id == 1
    assert relay.handle_bot_reply(first_reply).sent_message_id == 9001
    assert relay.handle_bot_reply(second_reply).sent_message_id == 9002


def test_reply_failover_records_events_and_prevents_duplicate_send(uow):
    account = create_active_account(uow)
    bot = FakeBotGateway()
    senders = FakeSenderPool({DeveloperSlot.PRIMARY})
    relay = PrivateRelayService(uow, bot, senders)
    pushed = relay.receive_private_message(
        IncomingPrivateMessage(account.id, 88, 99, MediaKind.TEXT, "hi", None, 0)
    )
    reply = OutgoingReply(1, 701, pushed.bot_message_id, MediaKind.TEXT, "reply")

    result = relay.handle_bot_reply(reply)
    duplicate = relay.handle_bot_reply(reply)

    assert result.sent_message_id == duplicate.sent_message_id
    assert senders.sent[0][0] is DeveloperSlot.STANDBY_1
    assert [event.to_slot for event in uow.failovers.list_for_account(account.id)] == [
        DeveloperSlot.STANDBY_1
    ]


def test_reply_fails_when_all_sessions_are_unavailable(uow):
    account = create_active_account(uow)
    relay = PrivateRelayService(uow, FakeBotGateway(), FakeSenderPool(set(DeveloperSlot)))
    pushed = relay.receive_private_message(
        IncomingPrivateMessage(account.id, 88, 100, MediaKind.STICKER, "sticker-id", None, 0)
    )

    with pytest.raises(SessionFailure, match="全部 session 不可用"):
        relay.handle_bot_reply(OutgoingReply(1, 702, pushed.bot_message_id, MediaKind.STICKER, "sticker-id"))

    events = uow.failovers.list_for_account(account.id)
    assert [event.status for event in events] == ["switched", "switched", "exhausted"]


@pytest.mark.parametrize("account_status", ["disabled", "deleted", "reauth_required"])
def test_incoming_rejects_unusable_account_before_any_relay_write(session_factory, account_status):
    with UnitOfWork(session_factory) as uow:
        account = create_active_account(uow)
        getattr(uow.accounts, f"mark_{account_status}")(account.id)
        uow.commit()
        account_id = account.id
    bot = FakeBotGateway()

    with pytest.raises(DeliveryFailure, match="account_unavailable"):
        with UnitOfWork(session_factory) as uow:
            PrivateRelayService(uow, bot, FakeSenderPool()).receive_private_message(
                IncomingPrivateMessage(account_id, 88, 999, MediaKind.TEXT, "blocked", None, 0)
            )

    with UnitOfWork(session_factory) as uow:
        assert uow.session.query(RelayMessageModel).count() == 0
        assert uow.session.query(BotPushMessageModel).count() == 0
        assert uow.session.query(ReplyMappingModel).count() == 0
    assert bot.pushes == []


def test_incoming_rejects_disabled_system_user_before_any_relay_write(session_factory):
    with UnitOfWork(session_factory) as uow:
        account = create_active_account(uow)
        uow.users.mark_disabled(account.system_user_id)
        uow.commit()
        account_id = account.id
    bot = FakeBotGateway()

    with pytest.raises(DeliveryFailure, match="system_user_disabled"):
        with UnitOfWork(session_factory) as uow:
            PrivateRelayService(uow, bot, FakeSenderPool()).receive_private_message(
                IncomingPrivateMessage(account_id, 88, 1000, MediaKind.TEXT, "blocked", None, 0)
            )

    with UnitOfWork(session_factory) as uow:
        assert uow.session.query(RelayMessageModel).count() == 0
        assert uow.session.query(BotPushMessageModel).count() == 0
        assert uow.session.query(ReplyMappingModel).count() == 0
    assert bot.pushes == []
