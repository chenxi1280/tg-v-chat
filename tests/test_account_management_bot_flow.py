import pytest
from sqlalchemy import BigInteger

from tg_v_chat.bot.account_management import AccountManagementService
from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotUpdateRouter
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeveloperSlot, MAX_BOUND_ACCOUNTS
from tg_v_chat.services.auth import AuthChallenge, AuthFailure, AuthStep
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.models import RelayMessageModel, ReplyMappingModel, SystemUserModel
from tg_v_chat.storage.repositories import UnitOfWork


class FakeAuthenticator:
    def __init__(self, *, needs_password=False, code_failure=None, password_failure=None):
        self.needs_password = needs_password
        self.code_failure = code_failure
        self.password_failure = password_failure
        self.started = []
        self.passwords = []

    def start(self, phone_number, slot):
        self.started.append((phone_number, slot))
        return AuthChallenge(phone_number, slot, "phone-code-hash")

    def complete_code(self, challenge, code):
        if self.code_failure is not None:
            raise self.code_failure
        if self.needs_password:
            return AuthStep.PASSWORD_REQUIRED
        return "session-string"

    def complete_password(self, challenge, password):
        if self.password_failure is not None:
            raise self.password_failure
        self.passwords.append(password)
        return "session-string-2fa"


@pytest.fixture()
def bot_parts():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    authenticator = FakeAuthenticator()
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    commands = []
    router = BotUpdateRouter(commands.append, service)
    return router, authenticator, commands, factory


def test_start_renders_account_management_home(bot_parts):
    router, _authenticator, _commands, _factory = bot_parts

    responses = router.handle(BotIncomingMessage(146517, 10, None, "/start"))

    assert len(responses) == 1
    assert responses[0].reply_to_message_id == 10
    assert "账号管理" in responses[0].text
    assert "还没有绑定" in responses[0].text
    assert [button.text for button in responses[0].buttons] == ["绑定 TG 账号", "中转说明", "帮助"]


def test_telegram_ids_use_big_integer_columns():
    assert isinstance(SystemUserModel.__table__.c.telegram_user_id.type, BigInteger)
    assert isinstance(RelayMessageModel.__table__.c.peer_id.type, BigInteger)
    assert isinstance(ReplyMappingModel.__table__.c.peer_id.type, BigInteger)


def test_start_accepts_real_large_telegram_user_id(bot_parts):
    router, _authenticator, _commands, factory = bot_parts

    responses = router.handle(BotIncomingMessage(7_677_366_761, 10, None, "/start"))

    assert "账号管理" in responses[0].text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(7_677_366_761)
        assert user.telegram_user_id == 7_677_366_761


def test_bind_button_then_phone_prompts_code(bot_parts):
    router, authenticator, _commands, _factory = bot_parts

    prompt = router.handle_callback(BotCallback(146517, "account.bind.start"))[0]
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]

    assert "手机号" in prompt.text
    assert "验证码已发送" in code_prompt.text
    assert authenticator.started == [("+15550000001", DeveloperSlot.PRIMARY)]


def test_code_and_password_complete_binding():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    authenticator = FakeAuthenticator(needs_password=True)
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    password_prompt = router.handle(BotIncomingMessage(146517, 12, None, "12345"))[0]
    success = router.handle(BotIncomingMessage(146517, 13, None, "secret"))[0]

    assert "2FA" in password_prompt.text
    assert "绑定成功" in success.text
    assert authenticator.passwords == ["secret"]
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        account = uow.accounts.list_for_user(user.id)[0]
        assert account.status == "active"
        assert len(uow.sessions.list_for_account(account.id)) == 3


def test_invalid_code_keeps_code_state():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    failure = AuthFailure("验证码不正确，请检查后重新输入。")
    authenticator = FakeAuthenticator(code_failure=failure)
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    response = router.handle(BotIncomingMessage(146517, 12, None, "12345"))[0]

    assert "验证码不正确" in response.text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        state = uow.conversation_states.get(user.id)
        assert state.state == "awaiting_code"


def test_expired_code_cancels_binding_state():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    failure = AuthFailure("验证码已过期，请重新开始绑定。", restart_required=True)
    authenticator = FakeAuthenticator(code_failure=failure)
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    response = router.handle(BotIncomingMessage(146517, 12, None, "12345"))[0]

    assert "验证码已过期" in response.text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        assert uow.conversation_states.get(user.id) is None
        account = uow.accounts.list_for_user(user.id)[0]
        assert account.status == "disabled"


def test_home_offers_relogin_for_abandoned_binding_state():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    authenticator = FakeAuthenticator()
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        uow.conversation_states.clear(user.id)
        uow.commit()

    response = router.handle(BotIncomingMessage(146517, 12, None, "/start"))[0]

    assert "已绑定账号：0/20" in response.text
    assert "需要处理：1" in response.text
    relogin_buttons = [button for button in response.buttons if button.text == "重新登录"]
    assert len(relogin_buttons) == 1
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        account = uow.accounts.list_for_user(user.id)[0]
        challenge = uow.auth_challenges.get(1)
        assert account.status == "binding"
        assert challenge.status == "code_required"


def test_relogin_restarts_abandoned_binding_with_same_phone():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    authenticator = FakeAuthenticator()
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        uow.conversation_states.clear(user.id)
        uow.commit()

    home = router.handle(BotIncomingMessage(146517, 12, None, "/start"))[0]
    relogin = next(button for button in home.buttons if button.text == "重新登录")
    response = router.handle_callback(BotCallback(146517, relogin.data))[0]

    assert "验证码已重新发送" in response.text
    assert authenticator.started == [
        ("+15550000001", DeveloperSlot.PRIMARY),
        ("+15550000001", DeveloperSlot.PRIMARY),
    ]
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        accounts = uow.accounts.list_for_user(user.id)
        state = uow.conversation_states.get(user.id)
        assert len(accounts) == 1
        assert accounts[0].phone_number == "+15550000001"
        assert accounts[0].status == "binding"
        challenges = uow.auth_challenges.list_for_account(accounts[0].id)
        assert len(challenges) == 1
        assert challenges[0].status == "code_required"
        assert state.state == "awaiting_code"
        assert state.auth_challenge_id == challenges[0].id


def test_disabled_accounts_do_not_block_new_binding():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    service = AccountManagementService(factory, FakeAuthenticator(), SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(146517)
        for index in range(MAX_BOUND_ACCOUNTS):
            account = uow.accounts.create(user.id, f"+1555000{index:04d}")
            uow.accounts.mark_disabled(account.id)
        uow.commit()

    response = router.handle_callback(BotCallback(146517, "account.bind.start"))[0]

    assert "请输入要管理的 TG 账号手机号" in response.text


def test_wrong_password_keeps_password_state():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    failure = AuthFailure("二次密码不正确，请重新输入。")
    authenticator = FakeAuthenticator(needs_password=True, password_failure=failure)
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    router.handle(BotIncomingMessage(146517, 12, None, "12345"))
    response = router.handle(BotIncomingMessage(146517, 13, None, "wrong"))[0]

    assert "二次密码不正确" in response.text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        state = uow.conversation_states.get(user.id)
        assert state.state == "awaiting_password"


def test_accounts_list_masks_phone_number(bot_parts):
    router, _authenticator, _commands, _factory = bot_parts
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    router.handle(BotIncomingMessage(146517, 12, None, "12345"))

    response = router.handle(BotIncomingMessage(146517, 13, None, "/accounts"))[0]

    assert "+15550****0001" in response.text
    assert "+15550000001" not in response.text
    assert response.buttons[0].data.startswith("account.detail:")


def test_account_detail_and_disable_are_scoped_to_owner(bot_parts):
    router, _authenticator, _commands, _factory = bot_parts
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    router.handle(BotIncomingMessage(146517, 12, None, "12345"))
    list_response = router.handle_callback(BotCallback(146517, "account.list"))[0]

    detail = router.handle_callback(BotCallback(146517, list_response.buttons[0].data))[0]
    confirm = router.handle_callback(BotCallback(146517, detail.buttons[0].data))[0]
    disabled = router.handle_callback(BotCallback(146517, confirm.buttons[0].data))[0]
    after_disable = router.handle_callback(BotCallback(146517, list_response.buttons[0].data))[0]

    assert "账号详情" in detail.text
    assert "确认禁用" in confirm.text
    assert "账号已禁用" in disabled.text
    assert "disabled" in after_disable.text


def test_reply_message_still_dispatches_relay_handler(bot_parts):
    router, _authenticator, commands, _factory = bot_parts

    responses = router.handle(BotIncomingMessage(146517, 20, 500, "收到"))

    assert responses == []
    assert commands[0].system_user_id == 146517
    assert commands[0].reply_to_message_id == 500
    assert commands[0].payload == "收到"
