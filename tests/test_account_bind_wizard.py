"""Account bind wizard: phone, code keypad, 2FA password, and failure handling tests."""
from account_management_helpers import FakeAuthenticator, RetryPasswordAuthenticator, submit_code_with_keypad
from tg_v_chat.bot.account_management import AccountManagementService
from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotUpdateRouter
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import MAX_BOUND_ACCOUNTS, DeveloperSlot
from tg_v_chat.services.auth import AuthFailure
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork


def _build_router(authenticator):
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    service = AccountManagementService(factory, authenticator, SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)
    return router, authenticator, factory


def test_bind_button_then_phone_prompts_code():
    router, authenticator, _factory = _build_router(FakeAuthenticator())

    prompt = router.handle_callback(BotCallback(146517, "account.bind.start"))[0]
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]

    assert "手机号" in prompt.text
    assert "验证码已发送" in code_prompt.text
    assert "不要直接发送验证码消息" in code_prompt.text
    assert [button.text for button in code_prompt.buttons] == [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "清空", "0", "删除",
        "✅ 提交", "🔄 重发验证码", "⬅️ 取消绑定",
    ]
    assert authenticator.started == [("+15550000001", DeveloperSlot.PRIMARY)]


def test_plain_code_message_is_rejected_in_code_state():
    router, authenticator, factory = _build_router(FakeAuthenticator())

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    response = router.handle(BotIncomingMessage(146517, 12, None, "12345"))[0]

    assert "不要直接发送验证码消息" in response.text
    assert authenticator.codes == []
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        state = uow.conversation_states.get(user.id)
        assert state.state == "awaiting_code"


def test_code_keypad_submits_buffered_code():
    router, authenticator, _factory = _build_router(FakeAuthenticator())

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    response = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    for digit in "12345":
        button = next(item for item in response.buttons if item.text == digit)
        response = router.handle_callback(BotCallback(146517, button.data))[0]
    submit = next(item for item in response.buttons if item.text == "✅ 提交")
    response = router.handle_callback(BotCallback(146517, submit.data))[0]

    assert "绑定成功" in response.text
    assert authenticator.codes == ["12345"]


def test_code_and_password_complete_binding():
    router, authenticator, factory = _build_router(FakeAuthenticator(needs_password=True))

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    password_prompt = submit_code_with_keypad(router, code_prompt)
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
    failure = AuthFailure("验证码不正确，请检查后重新输入。")
    router, _authenticator, factory = _build_router(FakeAuthenticator(code_failure=failure))

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    response = submit_code_with_keypad(router, code_prompt)

    assert "验证码不正确" in response.text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        state = uow.conversation_states.get(user.id)
        assert state.state == "awaiting_code"


def test_expired_code_cancels_binding_state():
    failure = AuthFailure("验证码已过期，请重新开始绑定。", restart_required=True)
    router, _authenticator, factory = _build_router(FakeAuthenticator(code_failure=failure))

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    response = submit_code_with_keypad(router, code_prompt)

    assert "验证码已过期" in response.text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        assert uow.conversation_states.get(user.id) is None
        account = uow.accounts.list_for_user(user.id)[0]
        challenge = uow.auth_challenges.list_for_account(account.id)[0]
        assert account.status == "binding"
        assert challenge.status == "expired"


def test_disabled_accounts_do_not_block_new_binding():
    router, _authenticator, factory = _build_router(FakeAuthenticator())
    with UnitOfWork(factory) as uow:
        user = uow.users.get_or_create(146517)
        for index in range(MAX_BOUND_ACCOUNTS):
            account = uow.accounts.create(user.id, f"+1555000{index:04d}")
            uow.accounts.mark_disabled(account.id)
        uow.commit()

    response = router.handle_callback(BotCallback(146517, "account.bind.start"))[0]

    assert "请输入要管理的 TG 账号手机号" in response.text


def test_wrong_password_keeps_password_state():
    failure = AuthFailure("二次密码不正确，请重新输入。")
    router, _authenticator, factory = _build_router(FakeAuthenticator(needs_password=True, password_failure=failure))

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, code_prompt)
    response = router.handle(BotIncomingMessage(146517, 13, None, "wrong"))[0]

    assert "二次密码不正确" in response.text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        state = uow.conversation_states.get(user.id)
        assert state.state == "awaiting_password"


def test_wrong_password_can_be_retried_successfully():
    router, authenticator, factory = _build_router(RetryPasswordAuthenticator())

    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, code_prompt)
    wrong = router.handle(BotIncomingMessage(146517, 13, None, "wrong"))[0]
    success = router.handle(BotIncomingMessage(146517, 14, None, "secret"))[0]

    assert "二次密码不正确" in wrong.text
    assert "绑定成功" in success.text
    assert authenticator.passwords == ["wrong", "secret"]
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        account = uow.accounts.list_for_user(user.id)[0]
        assert account.status == "active"
        assert uow.conversation_states.get(user.id) is None
