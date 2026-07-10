from __future__ import annotations

from tg_v_chat.domain import MediaKind
from tg_v_chat.storage.models import RelayMediaArtifactModel, RelayMediaGroupModel, utc_now


class MediaArtifactRepository:
    def __init__(self, session):
        self._session = session

    def create(
        self,
        *,
        direction: str,
        storage_key: str,
        file_name: str,
        mime_type: str,
        byte_size: int,
        media_kind: MediaKind,
        sequence: int,
        relay_message_id: int | None = None,
        outgoing_reply_id: int | None = None,
        metadata_json: str | None = None,
    ) -> RelayMediaArtifactModel:
        model = RelayMediaArtifactModel(
            relay_message_id=relay_message_id,
            outgoing_reply_id=outgoing_reply_id,
            direction=direction,
            storage_key=storage_key,
            file_name=file_name,
            mime_type=mime_type,
            byte_size=byte_size,
            media_kind=media_kind.value,
            sequence=sequence,
            metadata_json=metadata_json,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get_by_storage_key(self, storage_key: str) -> RelayMediaArtifactModel | None:
        return self._session.query(RelayMediaArtifactModel).filter_by(storage_key=storage_key).one_or_none()

    def list_releasable(self) -> list[RelayMediaArtifactModel]:
        return (
            self._session.query(RelayMediaArtifactModel)
            .filter(RelayMediaArtifactModel.status.in_(("sent", "failed")))
            .order_by(RelayMediaArtifactModel.id.asc())
            .all()
        )

    def mark_ready(self, storage_key: str) -> RelayMediaArtifactModel:
        return self._transition(storage_key, "ready", allowed_from=("staging",))

    def mark_sent(self, storage_key: str) -> RelayMediaArtifactModel:
        return self._transition(storage_key, "sent", allowed_from=("ready",))

    def mark_released(self, storage_key: str) -> RelayMediaArtifactModel:
        return self._transition(storage_key, "released", allowed_from=("sent", "failed"))

    def mark_failed(self, storage_key: str, reason: str) -> RelayMediaArtifactModel:
        return self._transition(
            storage_key,
            "failed",
            allowed_from=("staging", "ready"),
            failure_reason=reason,
        )

    def _transition(
        self,
        storage_key: str,
        target: str,
        *,
        allowed_from: tuple[str, ...],
        failure_reason: str | None = None,
    ) -> RelayMediaArtifactModel:
        values = {"status": target}
        if failure_reason is not None:
            values["failure_reason"] = failure_reason
        if target == "released":
            values["released_at"] = utc_now()
        changed = (
            self._session.query(RelayMediaArtifactModel)
            .filter(RelayMediaArtifactModel.storage_key == storage_key)
            .filter(RelayMediaArtifactModel.status.in_(allowed_from))
            .update(values, synchronize_session=False)
        )
        self._session.flush()
        if changed != 1:
            model = self._required(storage_key)
            raise ValueError(f"media artifact 状态不可转换: {model.status} -> {target}")
        return self._session.query(RelayMediaArtifactModel).filter_by(
            storage_key=storage_key
        ).populate_existing().one()

    def _required(self, storage_key: str) -> RelayMediaArtifactModel:
        model = self.get_by_storage_key(storage_key)
        if model is None:
            raise LookupError(f"media artifact 不存在: {storage_key}")
        return model


class MediaGroupRepository:
    def __init__(self, session):
        self._session = session

    def create(
        self,
        account_id: int,
        *,
        media_group_id: str,
        item_count: int,
        dispatch_key: str,
    ) -> RelayMediaGroupModel:
        model = RelayMediaGroupModel(
            bound_tg_account_id=account_id,
            media_group_id=media_group_id,
            item_count=item_count,
            dispatch_key=dispatch_key,
        )
        self._session.add(model)
        self._session.flush()
        return model

    def get_by_dispatch_key(self, dispatch_key: str) -> RelayMediaGroupModel | None:
        return self._session.query(RelayMediaGroupModel).filter_by(dispatch_key=dispatch_key).one_or_none()

    def get_for_account(self, account_id: int, media_group_id: str) -> RelayMediaGroupModel | None:
        return (
            self._session.query(RelayMediaGroupModel)
            .filter_by(bound_tg_account_id=account_id, media_group_id=media_group_id)
            .one_or_none()
        )

    def claim(self, dispatch_key: str) -> bool:
        changed = (
            self._session.query(RelayMediaGroupModel)
            .filter_by(dispatch_key=dispatch_key, status="pending")
            .update({"status": "sending", "updated_at": utc_now()}, synchronize_session="fetch")
        )
        self._session.flush()
        return changed == 1

    def mark_sent(self, dispatch_key: str) -> RelayMediaGroupModel:
        return self._transition(dispatch_key, "sent")

    def mark_failed(self, dispatch_key: str, code: str, reason: str) -> RelayMediaGroupModel:
        return self._transition(dispatch_key, "failed", code=code, reason=reason)

    def mark_uncertain(self, dispatch_key: str, code: str, reason: str) -> RelayMediaGroupModel:
        return self._transition(dispatch_key, "uncertain", code=code, reason=reason)

    def _transition(
        self,
        dispatch_key: str,
        status: str,
        *,
        code: str | None = None,
        reason: str | None = None,
    ) -> RelayMediaGroupModel:
        values = {
            "status": status,
            "failure_code": code,
            "failure_reason": reason,
            "updated_at": utc_now(),
        }
        changed = (
            self._session.query(RelayMediaGroupModel)
            .filter_by(dispatch_key=dispatch_key, status="sending")
            .update(values, synchronize_session=False)
        )
        self._session.flush()
        if changed != 1:
            model = self.get_by_dispatch_key(dispatch_key)
            if model is None:
                raise LookupError(f"media group dispatch 不存在: {dispatch_key}")
            raise ValueError(f"media group dispatch 状态不可终结: {model.status}")
        return self._session.query(RelayMediaGroupModel).filter_by(
            dispatch_key=dispatch_key
        ).populate_existing().one()
