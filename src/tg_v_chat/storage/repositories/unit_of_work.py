from __future__ import annotations

from tg_v_chat.storage.repositories.accounts import AccountRepository
from tg_v_chat.storage.repositories.auth import AuthChallengeRepository
from tg_v_chat.storage.repositories.conversation import ConversationStateRepository
from tg_v_chat.storage.repositories.dispatch import OutgoingReplyRepository, PushRepository
from tg_v_chat.storage.repositories.failover import FailoverRepository
from tg_v_chat.storage.repositories.locks import AccountOperationLock
from tg_v_chat.storage.repositories.media import MediaArtifactRepository, MediaGroupRepository
from tg_v_chat.storage.repositories.relay import MappingRepository, RelayRepository
from tg_v_chat.storage.repositories.sessions import SessionSlotRepository
from tg_v_chat.storage.repositories.users import UserRepository


class UnitOfWork:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self.account_locks = AccountOperationLock(session_factory.kw["bind"])
        self.user_locks = AccountOperationLock(session_factory.kw["bind"])

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
        self.media_artifacts = MediaArtifactRepository(self.session)
        self.media_groups = MediaGroupRepository(self.session)
        self.failovers = FailoverRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, _traceback) -> None:
        try:
            if exc_type:
                self.session.rollback()
        finally:
            self.session.close()

    def commit(self) -> None:
        self.session.commit()
