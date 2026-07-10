from __future__ import annotations

from dataclasses import dataclass

from tg_v_chat.domain import MediaArtifact, MediaKind, OutgoingReply


@dataclass(frozen=True)
class BotReplyCommand:
    system_user_id: int
    bot_reply_message_id: int
    reply_to_message_id: int | None
    media_kind: MediaKind
    payload: str
    artifacts: tuple[MediaArtifact, ...] = ()


class BotReplyHandler:
    def __init__(self, relay_service_factory):
        self._relay_service_factory = relay_service_factory

    def handle_reply(self, command: BotReplyCommand):
        reply = self._reply_from_command(command)
        with self._relay_service_factory() as relay_service:
            return relay_service.handle_bot_reply(reply)

    def _reply_from_command(self, command: BotReplyCommand) -> OutgoingReply:
        return OutgoingReply(
            system_user_id=command.system_user_id,
            bot_reply_message_id=command.bot_reply_message_id,
            reply_to_message_id=command.reply_to_message_id,
            media_kind=command.media_kind,
            payload=command.payload,
            artifacts=command.artifacts,
        )
