"""Account management home rendering, schema, and reply passthrough tests."""
from sqlalchemy import BigInteger

from account_management_helpers import bot_parts
from tg_v_chat.bot.router import BotIncomingMessage
from tg_v_chat.storage.models import RelayMessageModel, ReplyMappingModel, SystemUserModel
from tg_v_chat.storage.repositories import UnitOfWork


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


def test_reply_message_still_dispatches_relay_handler(bot_parts):
    router, _authenticator, commands, _factory = bot_parts

    responses = router.handle(BotIncomingMessage(146517, 20, 500, "收到"))

    assert responses == []
    assert commands[0].system_user_id == 146517
    assert commands[0].reply_to_message_id == 500
    assert commands[0].payload == "收到"
