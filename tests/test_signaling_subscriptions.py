# tests/test_signaling_subscriptions.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

from termcall.rooms import Member
from termcall.signaling import Signal, subscribe_room_members, subscribe_signals


def _room_members_client(table_data):
    client = MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value
    chain.is_.return_value.execute = AsyncMock(return_value=MagicMock(data=table_data))
    return client


def _signals_client(table_data):
    client = MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value.eq.return_value
    chain.execute = AsyncMock(return_value=MagicMock(data=table_data))
    return client


async def test_subscribe_room_members_selects_backlog_before_subscribing():
    client = _room_members_client(
        [{"id": 1, "room_id": "room-1", "user_id": "u1", "joined_at": "t1", "left_at": None}]
    )
    fake_channel = MagicMock()
    fake_channel.on_postgres_changes.side_effect = lambda *a, **k: calls.append("registered_callback") or fake_channel
    fake_channel.subscribe.side_effect = lambda *a, **k: calls.append("subscribed")
    client.channel.return_value = fake_channel

    calls = []

    async def on_member(member):
        calls.append(("member", member))

    backlog, channel = await subscribe_room_members(client, "room-1", on_member)

    assert backlog == [Member(id=1, room_id="room-1", user_id="u1", joined_at="t1", left_at=None)]
    assert calls == ["registered_callback", "subscribed"]  # backlog resolved before this call even runs
    assert channel is fake_channel


async def test_subscribe_room_members_dispatches_inserts_to_callback():
    client = _room_members_client([])
    fake_channel = MagicMock()
    captured_callback = {}

    def on_postgres_changes(*args, **kwargs):
        captured_callback["fn"] = kwargs["callback"]
        return fake_channel

    fake_channel.on_postgres_changes.side_effect = on_postgres_changes
    client.channel.return_value = fake_channel

    received = []

    async def on_member(member):
        received.append(member)

    await subscribe_room_members(client, "room-1", on_member)
    captured_callback["fn"]({"data": {"record": {"id": 2, "room_id": "room-1", "user_id": "u2", "joined_at": "t2", "left_at": None}}})

    await asyncio.sleep(0)
    assert received == [Member(id=2, room_id="room-1", user_id="u2", joined_at="t2", left_at=None)]


async def test_subscribe_signals_filters_backlog_by_recipient():
    client = _signals_client(
        [{"id": 1, "room_id": "room-1", "sender_id": "u1", "recipient_id": "u2", "kind": "offer", "payload": {"sdp": "x"}}]
    )
    client.channel.return_value = MagicMock()

    async def on_signal(signal):
        pass

    backlog, _channel = await subscribe_signals(client, "room-1", "u2", on_signal)
    assert backlog == [Signal(id=1, room_id="room-1", sender_id="u1", recipient_id="u2", kind="offer", payload={"sdp": "x"})]
