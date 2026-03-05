import pytest

from nanobot.bus.queue import MessageBus
from nanobot.channels.web import WebChannel


class _FakeWebSocket:
    def __init__(self, *, fail_send_json: bool = False) -> None:
        self.fail_send_json = fail_send_json
        self.sent: list[dict] = []
        self.closed_codes: list[int] = []

    async def send_json(self, payload: dict) -> None:
        if self.fail_send_json:
            raise RuntimeError("session payload send failed")
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed_codes.append(int(code))


@pytest.mark.asyncio
async def test_add_connection_session_payload_failure_prunes_state() -> None:
    channel = WebChannel(config=None, bus=MessageBus())
    session_id = "web:alice:deadbeef"
    ws = _FakeWebSocket(fail_send_json=True)

    with pytest.raises(RuntimeError):
        await channel.add_connection(session_id, ws, session_payload={"type": "session"})

    assert session_id not in channel.connections
    assert session_id not in channel._states


@pytest.mark.asyncio
async def test_remove_connection_prunes_inactive_state_entry() -> None:
    channel = WebChannel(config=None, bus=MessageBus())
    session_id = "web:alice:deadbeef"

    state = await channel._get_state(session_id, create=True)
    assert state is not None
    state.ws = None
    state.ready.set()

    await channel.remove_connection(session_id)

    assert session_id not in channel._states
