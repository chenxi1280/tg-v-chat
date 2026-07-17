import subprocess
import sys


def test_telethon_client_authenticator_import_does_not_load_listener_cycle():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tg_v_chat.telegram.telethon_clients.authenticator import SlotAuthenticatorRegistry",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
