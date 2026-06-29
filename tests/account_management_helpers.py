"""Shared fixtures and helpers for account management Bot flow tests."""
import pytest

from tg_v_chat.bot.account_management import AccountManagementService
from tg_v_chat.bot.router import BotCallback, BotIncomingMessage, BotUpdateRouter
from tg_v_chat.crypto import SessionCipher
from tg_v_chat.services.auth import AuthChallenge, AuthFailure, AuthStep
from tg_v_chat.storage.database import create_session_factory, init_db
from tg_v_chat.storage.repositories import UnitOfWork


class FakeAuthenticator:
    def __init__(self, *, needs_password=False, code_failure=None, password_failure=None):
        self.needs_password = needs_password
        self.code_failure = code_failure
        self.password_failure = password_failure
        self.started = []
        self.codes = []
        self.passwords = []

    def start(self, phone_number, slot):
        self.started.append((phone_number, slot))
        return AuthChallenge(phone_number, slot, "phone-code-hash")

    def complete_code(self, challenge, code):
        if self.code_failure is not None:
            raise self.code_failure
        self.codes.append(code)
        if self.needs_password:
            return AuthStep.PASSWORD_REQUIRED
        return "session-string"

    def complete_password(self, challenge, password):
        if self.password_failure is not None:
            raise self.password_failure
        self.passwords.append(password)
        return "session-string-2fa"


class RetryPasswordAuthenticator(FakeAuthenticator):
    def __init__(self):
        super().__init__(needs_password=True)

    def complete_password(self, challenge, password):
        self.passwords.append(password)
        if len(self.passwords) == 1:
            raise AuthFailure("二次密码不正确，请重新输入。")
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


def submit_code_with_keypad(router, response, code="12345", user_id=146517):
    for digit in code:
        button = next(item for item in response.buttons if item.text == digit)
        response = router.handle_callback(BotCallback(user_id, button.data))[0]
    submit = next(item for item in response.buttons if item.text == "✅ 提交")
    return router.handle_callback(BotCallback(user_id, submit.data))[0]


__all__ = [
    "FakeAuthenticator",
    "RetryPasswordAuthenticator",
    "bot_parts",
    "submit_code_with_keypad",
]
