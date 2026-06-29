"""Repository layer for tg-v-chat persistence."""
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
from tg_v_chat.storage.repositories.unit_of_work import UnitOfWork
from tg_v_chat.storage.repositories.users import UserRepository

__all__ = [
    "AccountRepository",
    "AuthChallengeRepository",
    "ConversationStateRepository",
    "FailoverRepository",
    "MappingRepository",
    "OutgoingReplyRepository",
    "PushRepository",
    "RelayRepository",
    "SessionSlotRepository",
    "UnitOfWork",
    "UserRepository",
]
