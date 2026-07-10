"""Account list, detail, disable, and delete tests."""
from account_management_helpers import FakeAuthenticator, bot_parts, submit_code_with_keypad
from tg_v_chat.bot.account_management import AccountManagementService
from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotUpdateRouter
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.domain import IncomingPrivateMessage, MediaKind
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork


def test_accounts_list_shows_account_name_and_username(bot_parts):
    router, _authenticator, _commands, _factory = bot_parts
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, code_prompt)

    response = router.handle(BotIncomingMessage(146517, 13, None, "/accounts"))[0]

    assert "小号A（@example_user）｜active" in response.text
    assert "+15550000001" not in response.text
    assert response.buttons[0].data.startswith("account.detail:")


def test_account_detail_shows_account_name_username_and_masked_phone(bot_parts):
    router, _authenticator, _commands, _factory = bot_parts
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, code_prompt)
    list_response = router.handle_callback(BotCallback(146517, "account.list"))[0]

    detail = router.handle_callback(BotCallback(146517, list_response.buttons[0].data))[0]

    assert "接收账号：小号A" in detail.text
    assert "用户名：@example_user" in detail.text
    assert "手机号：+15550****0001" in detail.text


def test_account_detail_and_disable_are_scoped_to_owner(bot_parts):
    router, _authenticator, _commands, factory = bot_parts
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, code_prompt)
    list_response = router.handle_callback(BotCallback(146517, "account.list"))[0]
    _create_mapping(factory, account_id=1, bot_message_id=500)

    detail = router.handle_callback(BotCallback(146517, list_response.buttons[0].data))[0]
    disable_button = next(button for button in detail.buttons if button.text == "禁用账号")
    confirm = router.handle_callback(BotCallback(146517, disable_button.data))[0]
    disabled = router.handle_callback(BotCallback(146517, confirm.buttons[0].data))[0]
    after_disable = router.handle_callback(BotCallback(146517, list_response.buttons[0].data))[0]

    assert "账号详情" in detail.text
    assert "确认禁用" in confirm.text
    assert "账号已禁用" in disabled.text
    assert "disabled" in after_disable.text
    with UnitOfWork(factory) as uow:
        mapping = uow.mappings.get_by_bot_message(1, 500)
        assert mapping.status == "expired"
        assert mapping.invalidated_at is not None


def test_active_account_can_be_deleted_from_detail(bot_parts):
    router, _authenticator, _commands, factory = bot_parts
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    code_prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, code_prompt)
    list_response = router.handle_callback(BotCallback(146517, "account.list"))[0]
    _create_mapping(factory, account_id=1, bot_message_id=501)

    detail = router.handle_callback(BotCallback(146517, list_response.buttons[0].data))[0]
    delete_button = next(button for button in detail.buttons if button.text == "删除账号")
    confirm = router.handle_callback(BotCallback(146517, delete_button.data))[0]
    deleted = router.handle_callback(BotCallback(146517, confirm.buttons[0].data))[0]
    after_delete = router.handle_callback(BotCallback(146517, "account.list"))[0]

    assert "确认删除" in confirm.text
    assert "账号已删除" in deleted.text
    assert "暂无绑定账号" in after_delete.text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        account = uow.accounts.get(1)
        mapping = uow.mappings.get_by_bot_message(user.id, 501)
        assert account.status == "deleted"
        assert mapping.status == "expired"
        assert mapping.invalidated_at is not None
        assert uow.sessions.list_for_account(account.id) == []
        assert uow.accounts.list_for_user(user.id) == []


def test_incomplete_account_delete_removes_account_and_challenge():
    factory = create_session_factory("sqlite:///:memory:")
    init_db(factory)
    service = AccountManagementService(factory, FakeAuthenticator(), SessionCipher("test-key"))
    router = BotUpdateRouter(lambda _command: None, service)
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))
    list_response = router.handle_callback(BotCallback(146517, "account.list"))[0]

    detail = router.handle_callback(BotCallback(146517, list_response.buttons[0].data))[0]
    delete_button = next(button for button in detail.buttons if button.text == "删除账号")
    confirm = router.handle_callback(BotCallback(146517, delete_button.data))[0]
    deleted = router.handle_callback(BotCallback(146517, confirm.buttons[0].data))[0]

    assert "账号已删除" in deleted.text
    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        assert uow.accounts.list_for_user(user.id) == []
        assert uow.auth_challenges.list_for_account(1) == []


def test_disabled_account_delete_keeps_expired_mapping_history(bot_parts):
    router, _authenticator, _commands, factory = bot_parts
    router.handle_callback(BotCallback(146517, "account.bind.start"))
    prompt = router.handle(BotIncomingMessage(146517, 11, None, "+15550000001"))[0]
    submit_code_with_keypad(router, prompt)
    _create_mapping(factory, account_id=1, bot_message_id=502)

    router.handle_callback(BotCallback(146517, "account.disable:1"))
    router.handle_callback(BotCallback(146517, "account.delete:1"))

    with UnitOfWork(factory) as uow:
        user = uow.users.get_by_telegram_id(146517)
        account = uow.accounts.get(1)
        mapping = uow.mappings.get_by_bot_message(user.id, 502)
        assert account.status == "deleted"
        assert mapping.status == "expired"
        assert mapping.invalidated_at is not None


def _create_mapping(factory, *, account_id: int, bot_message_id: int) -> None:
    with UnitOfWork(factory) as uow:
        account = uow.accounts.get(account_id)
        relay, _ = uow.relays.create_or_get(
            IncomingPrivateMessage(account.id, 88, bot_message_id, MediaKind.TEXT, "hi", None, 0)
        )
        uow.mappings.create(bot_message_id, relay, account.system_user_id)
        uow.commit()
