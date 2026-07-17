"""Repository layer for tg-v-chat persistence."""
from tg_v_chat.storage.repositories.accounts import AccountRepository
from tg_v_chat.storage.repositories.auth import AuthChallengeRepository
from tg_v_chat.storage.repositories.conversation import ConversationStateRepository
from tg_v_chat.storage.repositories.dispatch import OutgoingReplyRepository, PushRepository
from tg_v_chat.storage.repositories.failover import FailoverRepository
from tg_v_chat.storage.repositories.locks import AccountOperationLock, TelegramIdentityLock
from tg_v_chat.storage.repositories.media import MediaArtifactRepository, MediaGroupRepository
from tg_v_chat.storage.repositories.native_forward import NativeForwardRepository
from tg_v_chat.storage.repositories.relay import MappingRepository, RelayRepository
from tg_v_chat.storage.repositories.sessions import SessionSlotRepository
from tg_v_chat.storage.repositories.unit_of_work import UnitOfWork
from tg_v_chat.storage.repositories.users import UserRepository

__all__ = [
    "AccountRepository",
    "AccountOperationLock",
    "AuthChallengeRepository",
    "ConversationStateRepository",
    "FailoverRepository",
    "MappingRepository",
    "MediaArtifactRepository",
    "MediaGroupRepository",
    "NativeForwardRepository",
    "OutgoingReplyRepository",
    "PushRepository",
    "RelayRepository",
    "SessionSlotRepository",
    "TelegramIdentityLock",
    "UnitOfWork",
    "UserRepository",
]
