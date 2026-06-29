from __future__ import annotations

from tg_v_chat.storage.repositories.accounts import AccountRepository
from tg_v_chat.storage.repositories.auth import AuthChallengeRepository
from tg_v_chat.storage.repositories.conversation import ConversationStateRepository
from tg_v_chat.storage.repositories.failover import FailoverRepository
from tg_v_chat.storage.repositories.relay import (
    MappingRepository,
    OutgoingReplyRepository,
    PushRepository,
    RelayRepository,
)
from tg_v_chat.storage.repositories.sessions import SessionSlotRepository
from tg_v_chat.storage.repositories.users import UserRepository


class UnitOfWork:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def __enter__(self):
        self.session = self._session_factory()
        self.users = UserRepository(self.session)
        self.accounts = AccountRepository(self.session)
        self.sessions = SessionSlotRepository(self.session)
        self.auth_challenges = AuthChallengeRepository(self.session)
        self.conversation_states = ConversationStateRepository(self.session)
        self.relays = RelayRepository(self.session)
        self.pushes = PushRepository(self.session)
        self.mappings = MappingRepository(self.session)
        self.outgoing = OutgoingReplyRepository(self.session)
        self.failovers = FailoverRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, _traceback) -> None:
        self.session.rollback() if exc_type else None
        self.session.close()

    def commit(self) -> None:
        self.session.commit()
