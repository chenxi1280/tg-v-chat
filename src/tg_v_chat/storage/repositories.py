from __future__ import annotations

from tg_v_chat.domain import DeveloperSlot, IncomingPrivateMessage, SessionStatus
from tg_v_chat.storage.models import (
    AuthChallengeModel,
    BotConversationStateModel,
    BotPushMessageModel,
    BoundTgAccountModel,
    OutgoingReplyModel,
    RelayMessageModel,
    ReplyMappingModel,
    SessionFailoverEventModel,
    SystemUserModel,
    TgSessionSlotModel,
)


class UserRepository:
    def __init__(self, session):
        self._session = session

    def get_or_create(self, telegram_user_id: int) -> SystemUserModel:
        user = self._session.query(SystemUserModel).filter_by(telegram_user_id=telegram_user_id).one_or_none()
        if user:
            return user
        user = SystemUserModel(telegram_user_id=telegram_user_id)
        self._session.add(user)
        self._session.flush()
        return user

    def get(self, user_id: int) -> SystemUserModel:
        user = self._session.get(SystemUserModel, user_id)
        if not user:
            raise LookupError(f"SystemUser 不存在: {user_id}")
        return user

    def get_by_telegram_id(self, telegram_user_id: int) -> SystemUserModel:
        user = self._session.query(SystemUserModel).filter_by(telegram_user_id=telegram_user_id).one_or_none()
        if not user:
            raise LookupError(f"SystemUser 不存在: {telegram_user_id}")
        return user


class AccountRepository:
    def __init__(self, session):
        self._session = session

    def count_for_user(self, system_user_id: int) -> int:
        return self._session.query(BoundTgAccountModel).filter_by(system_user_id=system_user_id).count()

    def list_for_user(self, system_user_id: int) -> list[BoundTgAccountModel]:
        return (
            self._session.query(BoundTgAccountModel)
            .filter_by(system_user_id=system_user_id)
            .order_by(BoundTgAccountModel.id.asc())
            .all()
        )

    def create(self, system_user_id: int, phone_number: str) -> BoundTgAccountModel:
        account = BoundTgAccountModel(system_user_id=system_user_id, phone_number=phone_number)
        self._session.add(account)
        self._session.flush()
        return account

    def get(self, account_id: int) -> BoundTgAccountModel:
        account = self._session.get(BoundTgAccountModel, account_id)
        if not account:
            raise LookupError(f"BoundTgAccount 不存在: {account_id}")
        return account

    def get_for_user(self, account_id: int, system_user_id: int) -> BoundTgAccountModel:
        account = self._session.get(BoundTgAccountModel, account_id)
        if not account or account.system_user_id != system_user_id:
            raise LookupError(f"BoundTgAccount 不存在: {account_id}")
        return account

    def mark_active(self, account_id: int) -> BoundTgAccountModel:
        account = self.get(account_id)
        account.status = "active"
        self._session.flush()
        return account

    def mark_disabled(self, account_id: int) -> BoundTgAccountModel:
        account = self.get(account_id)
        account.status = "disabled"
        self._session.flush()
        return account


class SessionSlotRepository:
    def __init__(self, session):
        self._session = session

    def create(self, account_id: int, slot: DeveloperSlot, encrypted_session: str, status: SessionStatus):
        model = TgSessionSlotModel(
            bound_tg_account_id=account_id,
            developer_slot=slot.value,
            encrypted_session=encrypted_session,
            status=status.value,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def list_for_account(self, account_id: int) -> list[TgSessionSlotModel]:
        rows = self._session.query(TgSessionSlotModel).filter_by(bound_tg_account_id=account_id).all()
        order = {slot.value: index for index, slot in enumerate(DeveloperSlot)}
        return sorted(rows, key=lambda row: order[row.developer_slot])

    def mark_failed(self, slot_id: int, reason: str) -> None:
        model = self._session.get(TgSessionSlotModel, slot_id)
        if not model:
            raise LookupError(f"TgSessionSlot 不存在: {slot_id}")
        model.status = SessionStatus.FAILED.value
        model.failure_reason = reason
        self._session.flush()


class AuthChallengeRepository:
    def __init__(self, session):
        self._session = session

    def create(self, account_id: int, phone: str, slot: DeveloperSlot, code_hash: str):
        model = AuthChallengeModel(
            bound_tg_account_id=account_id,
            phone_number=phone,
            developer_slot=slot.value,
            phone_code_hash=code_hash,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get(self, challenge_id: int) -> AuthChallengeModel:
        challenge = self._session.get(AuthChallengeModel, challenge_id)
        if not challenge:
            raise LookupError(f"AuthChallenge 不存在: {challenge_id}")
        return challenge

    def mark_status(self, challenge_id: int, status: str) -> AuthChallengeModel:
        challenge = self.get(challenge_id)
        challenge.status = status
        self._session.flush()
        return challenge


class ConversationStateRepository:
    def __init__(self, session):
        self._session = session

    def get(self, system_user_id: int) -> BotConversationStateModel | None:
        return self._session.query(BotConversationStateModel).filter_by(system_user_id=system_user_id).one_or_none()

    def set(self, system_user_id: int, state: str, challenge_id: int | None = None) -> BotConversationStateModel:
        existing = self.get(system_user_id)
        if existing:
            existing.state = state
            existing.auth_challenge_id = challenge_id
            self._session.flush()
            return existing
        model = BotConversationStateModel(
            system_user_id=system_user_id,
            state=state,
            auth_challenge_id=challenge_id,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def clear(self, system_user_id: int) -> None:
        existing = self.get(system_user_id)
        if existing:
            self._session.delete(existing)
            self._session.flush()


class RelayRepository:
    def __init__(self, session):
        self._session = session

    def create_or_get(self, message: IncomingPrivateMessage) -> tuple[RelayMessageModel, bool]:
        existing = self._session.query(RelayMessageModel).filter_by(
            bound_tg_account_id=message.bound_tg_account_id,
            source_message_id=message.source_message_id,
        ).one_or_none()
        if existing:
            return existing, True
        model = RelayMessageModel(**_relay_values(message))
        self._session.add(model)
        self._session.flush()
        return model, False

    def list_media_group(self, account_id: int, media_group_id: str) -> list[RelayMessageModel]:
        rows = self._session.query(RelayMessageModel).filter_by(
            bound_tg_account_id=account_id,
            media_group_id=media_group_id,
        ).all()
        return sorted(rows, key=lambda row: row.sequence)

    def has_media_sequence(self, account_id: int, media_group_id: str, sequence: int) -> bool:
        row = self._session.query(RelayMessageModel).filter_by(
            bound_tg_account_id=account_id,
            media_group_id=media_group_id,
            sequence=sequence,
        ).one_or_none()
        return row is not None


class PushRepository:
    def __init__(self, session):
        self._session = session

    def create(self, relay_id: int, system_user_id: int, bot_message_id: int) -> BotPushMessageModel:
        model = BotPushMessageModel(
            relay_message_id=relay_id,
            system_user_id=system_user_id,
            bot_message_id=bot_message_id,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get_by_relay(self, relay_id: int) -> BotPushMessageModel | None:
        return self._session.query(BotPushMessageModel).filter_by(relay_message_id=relay_id).one_or_none()


class MappingRepository:
    def __init__(self, session):
        self._session = session

    def create(self, bot_message_id: int, relay: RelayMessageModel, system_user_id: int) -> ReplyMappingModel:
        model = ReplyMappingModel(
            bot_message_id=bot_message_id,
            system_user_id=system_user_id,
            relay_message_id=relay.id,
            bound_tg_account_id=relay.bound_tg_account_id,
            peer_id=relay.peer_id,
            source_message_id=relay.source_message_id,
            media_kind=relay.media_kind,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get_by_bot_message(self, bot_message_id: int) -> ReplyMappingModel | None:
        return self._session.query(ReplyMappingModel).filter_by(bot_message_id=bot_message_id).one_or_none()


class OutgoingReplyRepository:
    def __init__(self, session):
        self._session = session

    def create(
        self,
        reply_id: int,
        system_user_id: int,
        relay_id: int,
        sent_id: int,
        slot: DeveloperSlot,
    ) -> OutgoingReplyModel:
        model = OutgoingReplyModel(
            bot_reply_message_id=reply_id,
            system_user_id=system_user_id,
            relay_message_id=relay_id,
            sent_message_id=sent_id,
            developer_slot=slot.value,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get_by_reply(self, reply_id: int) -> OutgoingReplyModel | None:
        return self._session.query(OutgoingReplyModel).filter_by(bot_reply_message_id=reply_id).one_or_none()


class FailoverRepository:
    def __init__(self, session):
        self._session = session

    def create(self, account_id: int, from_slot: DeveloperSlot, to_slot: DeveloperSlot, reason: str):
        model = SessionFailoverEventModel(
            bound_tg_account_id=account_id,
            from_slot=from_slot.value,
            to_slot=to_slot.value,
            status="switched",
            reason=reason,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def create_exhausted(self, account_id: int, from_slot: DeveloperSlot, reason: str):
        model = SessionFailoverEventModel(
            bound_tg_account_id=account_id,
            from_slot=from_slot.value,
            to_slot=from_slot.value,
            status="exhausted",
            reason=reason,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def list_for_account(self, account_id: int) -> list[SessionFailoverEventModel]:
        return self._session.query(SessionFailoverEventModel).filter_by(bound_tg_account_id=account_id).all()


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


def _relay_values(message: IncomingPrivateMessage) -> dict:
    return {
        "bound_tg_account_id": message.bound_tg_account_id,
        "peer_id": message.peer_id,
        "source_message_id": message.source_message_id,
        "media_kind": message.media_kind.value,
        "payload": message.payload,
        "media_group_id": message.media_group_id,
        "sequence": message.sequence,
    }
