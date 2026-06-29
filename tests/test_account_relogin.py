"""Account relogin flow: abandoned binding recovery and re-bind tests."""
from account_management_helpers import FakeAuthenticator, submit_code_with_keypad
from tg_v_chat.bot.account_management import AccountManagementService
from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotUpdateRouter
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import DeveloperSlot
from tg_v_chat.services.auth import AuthFailure
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork


def _build_router(authenticator=None):
    if authenticator is None:
        authenticator = FakeAuthenticator()
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)
    return router, authenticator, factory


def test_expired_code_remains_pending_on_home():
    failure = AuthFailure("验证码已过期，请重新开始绑定。", restart_required=True)
    router, _authenticator, factory = _build_router(FakeAuthenticator(code_failure=failure))

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, code_prompt)
    home = router.handle(BotIncomingMessage(146517, 12, None, "/start"))[0]

    assert "需要处理：1" in home.text
    assert any(button.text == "重新登录" for button in home.buttons)
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        account = uow.accounts.list_for_user(user.id)[0]
        challenge = uow.auth_challenges.list_for_account(account.id)[0]
        assert account.status == "binding"
        assert challenge.status == "expired"


def test_rebinding_same_failed_phone_reuses_incomplete_account():
    failure = AuthFailure("验证码已过期，请重新开始绑定。", restart_required=True)
    router, _authenticator, factory = _build_router(FakeAuthenticator(code_failure=failure))

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, code_prompt)
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 12, None, "+15550000001"))

    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        accounts = uow.accounts.list_for_user(user.id)
        assert len(accounts) == 1
        assert accounts[0].phone_number == "+15550000001"
        assert accounts[0].status == "binding"
        assert len(uow.auth_challenges.list_for_account(accounts[0].id)) == 1


def test_home_offers_relogin_for_abandoned_binding_state():
    router, _authenticator, factory = _build_router()

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
    router, authenticator, factory = _build_router()

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        uow.conversation_states.clear(user.id)
        uow.commit()

    home = router.handle(BotIncomingMessage(146517, 12, None, "/start"))[0]
    relogin = next(button for button in home.buttons if button.text == "重新登录")
    response = router.handle_callback(BotCallback(146517, relogin.data))[0]

    _assert_relogin_code_prompt(response, authenticator)
    with UnitOfWork(factory) as uow:
        _assert_single_restarted_binding(uow)


def _assert_relogin_code_prompt(response, authenticator) -> None:
    assert "验证码已重新发送" in response.text
    assert "不要直接发送验证码消息" in response.text
    assert [button.text for button in response.buttons[:3]] == ["1", "2", "3"]
    assert any(button.text == "✅ 提交" for button in response.buttons)
    assert authenticator.started == [
        ("+15550000001", DeveloperSlot.PRIMARY),
        ("+15550000001", DeveloperSlot.PRIMARY),
    ]


def _assert_single_restarted_binding(uow) -> None:
    user = uow.users.get_by_telegram_id(146517)
    accounts = uow.accounts.list_for_user(user.id)
    state = uow.conversation_states.get(user.id)
    challenges = uow.auth_challenges.list_for_account(accounts[0].id)

    assert len(accounts) == 1
    assert accounts[0].phone_number == "+15550000001"
    assert accounts[0].status == "binding"
    assert len(challenges) == 1
    assert challenges[0].status == "code_required"
    assert state.state == "awaiting_code"
    assert state.auth_challenge_id == challenges[0].id
