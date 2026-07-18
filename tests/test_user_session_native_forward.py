import pytest

from tg_v_chat.domain import DeliveryFailure, DeliveryUncertain, NativeForwardRequest, TelegramPeer
from tg_v_chat.telegram.private_listener.native_forward import (
    TelethonUserSessionForwarder,
    _forward_error,
    marker_text,
    parse_marker,
)


class Client:
    def __init__(self):
        self.calls = []

    async def get_input_entity(self, target):
        self.calls.append(("resolve", target))
        return "bot-peer"

    async def send_message(self, target, text):
        self.calls.append(("marker", target, text))
        return type("Sent", (), {"id": 501})()

    async def forward_messages(self, target, message_ids, *, from_peer):
        self.calls.append(("forward", target, tuple(message_ids), from_peer))
        return [type("Sent", (), {"id": 601})(), type("Sent", (), {"id": 602})()]


def _request(peer=TelegramPeer(88, 9901)):
    return NativeForwardRequest(1, "marker-token", 2, peer, (11, 12))


def test_user_session_resolves_configured_bot_and_forwards_original_ids():
    client = Client()
    forwarder = TelethonUserSessionForwarder(client, "relay_bot")

    result = forwarder.forward_batch(_request())

    assert result.forwarded_count == 2
    assert client.calls[0] == ("resolve", "@relay_bot")
    assert client.calls[1] == ("marker", "bot-peer", marker_text(_request()))
    assert client.calls[2][0:3] == ("forward", "bot-peer", (11, 12))
    assert client.calls[2][3].user_id == 88
    assert client.calls[2][3].access_hash == 9901


def test_user_session_refuses_missing_source_access_hash_without_copy_fallback():
    forwarder = TelethonUserSessionForwarder(Client(), "relay_bot")

    with pytest.raises(DeliveryFailure, match="native_forward_peer_invalid"):
        forwarder.forward_batch(_request(TelegramPeer(88)))


def test_user_session_marks_missing_forward_result_uncertain():
    class MissingResultClient(Client):
        async def forward_messages(self, _target, _message_ids, *, from_peer):
            return None

    forwarder = TelethonUserSessionForwarder(MissingResultClient(), "relay_bot")

    with pytest.raises(DeliveryUncertain, match="bridge_transport_unknown"):
        forwarder.forward_batch(_request())


def test_bridge_marker_has_a_parseable_fixed_contract():
    marker = marker_text(_request())

    parsed = parse_marker(marker)

    assert parsed is not None
    assert parsed.token == "marker-token"
    assert parsed.expected_count == 2
    assert parse_marker("tgvc-forward-v2:bad") is None


def test_restricted_and_missing_source_errors_keep_explicit_native_codes():
    from telethon.errors import ChatForwardsRestrictedError, MessageIdInvalidError

    assert _forward_error(ChatForwardsRestrictedError(None)).code == "native_forward_restricted"
    assert _forward_error(MessageIdInvalidError(None)).code == "native_forward_source_missing"
