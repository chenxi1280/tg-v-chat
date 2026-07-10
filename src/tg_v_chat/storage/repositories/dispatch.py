from __future__ import annotations

from tg_v_chat.domain import DeveloperSlot
from tg_v_chat.storage.models import BotPushMessageModel, OutgoingReplyModel, utc_now


def _claim(session, model_type, dispatch_key: str) -> bool:
    changed = (
        session.query(model_type)
        .filter_by(dispatch_key=dispatch_key, status="pending")
        .update({"status": "sending", "updated_at": utc_now()}, synchronize_session="fetch")
    )
    session.flush()
    return changed == 1


def _transition(session, model_type, dispatch_key: str, *, values: dict):
    changed = (
        session.query(model_type)
        .filter_by(dispatch_key=dispatch_key, status="sending")
        .update({**values, "updated_at": utc_now()}, synchronize_session=False)
    )
    session.flush()
    if changed != 1:
        _raise_terminal_conflict(session, model_type, dispatch_key)
    return session.query(model_type).filter_by(dispatch_key=dispatch_key).populate_existing().one()


def _raise_terminal_conflict(session, model_type, dispatch_key: str) -> None:
    row = session.query(model_type).filter_by(dispatch_key=dispatch_key).one_or_none()
    if row is None:
        raise LookupError(f"dispatch 不存在: {dispatch_key}")
    raise ValueError(f"dispatch 状态不可终结: {row.status}")


class PushRepository:
    def __init__(self, session):
        self._session = session

    def create_pending(self, relay_id: int, system_user_id: int, dispatch_key: str) -> BotPushMessageModel:
        model = BotPushMessageModel(
            relay_message_id=relay_id,
            system_user_id=system_user_id,
            dispatch_key=dispatch_key,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def create(self, relay_id: int, system_user_id: int, bot_message_id: int) -> BotPushMessageModel:
        model = self.create_pending(relay_id, system_user_id, f"push:{relay_id}")
        model.status = "sent"
        model.bot_message_id = bot_message_id
        self._session.flush()
        return model

    def get_by_relay(self, relay_id: int) -> BotPushMessageModel | None:
        return self._session.query(BotPushMessageModel).filter_by(relay_message_id=relay_id).one_or_none()

    def get_by_dispatch_key(self, dispatch_key: str) -> BotPushMessageModel | None:
        return self._session.query(BotPushMessageModel).filter_by(dispatch_key=dispatch_key).one_or_none()

    def claim(self, dispatch_key: str) -> bool:
        return _claim(self._session, BotPushMessageModel, dispatch_key)

    def mark_sent(self, dispatch_key: str, *, bot_message_id: int) -> BotPushMessageModel:
        return _transition(
            self._session,
            BotPushMessageModel,
            dispatch_key,
            values={"status": "sent", "bot_message_id": bot_message_id},
        )

    def mark_failed(self, dispatch_key: str, code: str, reason: str) -> BotPushMessageModel:
        return self._mark_error(dispatch_key, code, reason, status="failed")

    def mark_uncertain(self, dispatch_key: str, code: str, reason: str) -> BotPushMessageModel:
        return self._mark_error(dispatch_key, code, reason, status="uncertain")

    def _mark_error(self, dispatch_key: str, code: str, reason: str, *, status: str):
        values = {"status": status, "failure_code": code, "failure_reason": reason}
        return _transition(self._session, BotPushMessageModel, dispatch_key, values=values)


class OutgoingReplyRepository:
    def __init__(self, session):
        self._session = session

    def create_pending(
        self,
        reply_id: int,
        *,
        system_user_id: int,
        relay_id: int,
        dispatch_key: str,
    ) -> OutgoingReplyModel:
        model = OutgoingReplyModel(
            bot_reply_message_id=reply_id,
            system_user_id=system_user_id,
            relay_message_id=relay_id,
            dispatch_key=dispatch_key,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def create(
        self,
        reply_id: int,
        *,
        system_user_id: int,
        relay_id: int,
        sent_id: int,
        slot: DeveloperSlot,
    ) -> OutgoingReplyModel:
        model = self.create_pending(
            reply_id,
            system_user_id=system_user_id,
            relay_id=relay_id,
            dispatch_key=f"outgoing:{system_user_id}:{reply_id}",
        )
        model.sent_message_id = sent_id
        model.developer_slot = slot.value
        model.status = "sent"
        self._session.flush()
        return model

    def get_by_reply(self, system_user_id: int, reply_id: int) -> OutgoingReplyModel | None:
        return (
            self._session.query(OutgoingReplyModel)
            .filter_by(system_user_id=system_user_id, bot_reply_message_id=reply_id)
            .one_or_none()
        )

    def get_by_dispatch_key(self, dispatch_key: str) -> OutgoingReplyModel | None:
        return self._session.query(OutgoingReplyModel).filter_by(dispatch_key=dispatch_key).one_or_none()

    def claim(self, dispatch_key: str) -> bool:
        return _claim(self._session, OutgoingReplyModel, dispatch_key)

    def mark_sent(
        self,
        dispatch_key: str,
        *,
        sent_message_id: int,
        developer_slot: DeveloperSlot,
    ) -> OutgoingReplyModel:
        values = {
            "status": "sent",
            "sent_message_id": sent_message_id,
            "developer_slot": developer_slot.value,
        }
        return _transition(self._session, OutgoingReplyModel, dispatch_key, values=values)

    def mark_failed(self, dispatch_key: str, code: str, reason: str) -> OutgoingReplyModel:
        return self._mark_error(dispatch_key, code, reason, status="failed")

    def mark_uncertain(self, dispatch_key: str, code: str, reason: str) -> OutgoingReplyModel:
        return self._mark_error(dispatch_key, code, reason, status="uncertain")

    def _mark_error(self, dispatch_key: str, code: str, reason: str, *, status: str):
        values = {"status": status, "failure_code": code, "failure_reason": reason}
        return _transition(self._session, OutgoingReplyModel, dispatch_key, values=values)
