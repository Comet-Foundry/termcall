# tests/test_rooms.py
from unittest.mock import AsyncMock, MagicMock

import pytest
from postgrest.exceptions import APIError

from termcall.rooms import (
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    Member,
    Room,
    RoomFullError,
    RoomNotJoinableError,
    create_room,
    fetch_active_roster,
    generate_room_code,
    join_room,
    leave_room,
)


def test_generate_room_code_uses_unambiguous_alphabet_and_requested_length():
    code = generate_room_code()
    assert len(code) == ROOM_CODE_LENGTH
    assert all(c in ROOM_CODE_ALPHABET for c in code)
    assert "0" not in code and "O" not in code and "1" not in code and "I" not in code


def _table_mock(execute_result):
    table = MagicMock()
    table.insert.return_value.execute = AsyncMock(return_value=execute_result)
    table.select.return_value.eq.return_value.is_.return_value.order.return_value.execute = AsyncMock(
        return_value=execute_result
    )
    return table


async def test_create_room_inserts_then_joins_as_first_member():
    client = MagicMock()
    insert_result = MagicMock(data=[{"id": "room-1", "code": "ABC123", "host_id": "host-1", "status": "open"}])
    client.table.return_value = _table_mock(insert_result)
    client.rpc.return_value.execute = AsyncMock()

    room = await create_room(client, "host-1")

    assert room == Room(id="room-1", code="ABC123", host_id="host-1", status="open")
    client.rpc.assert_called_once_with("join_room", {"p_code": "ABC123"})


async def test_join_room_maps_room_full_error():
    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock(side_effect=APIError({"message": "room full"}))
    with pytest.raises(RoomFullError):
        await join_room(client, "ABC123")


async def test_join_room_maps_room_not_joinable_error():
    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock(side_effect=APIError({"message": "room not joinable"}))
    with pytest.raises(RoomNotJoinableError):
        await join_room(client, "ABC123")


async def test_join_room_returns_room_on_success():
    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock(
        return_value=MagicMock(data={"id": "room-1", "code": "ABC123", "host_id": "host-1", "status": "open"})
    )
    room = await join_room(client, "ABC123")
    assert room == Room(id="room-1", code="ABC123", host_id="host-1", status="open")


async def test_leave_room_calls_rpc():
    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock()
    await leave_room(client, "room-1")
    client.rpc.assert_called_once_with("leave_room", {"p_room_id": "room-1"})


async def test_fetch_active_roster_parses_members_ordered_by_join():
    client = MagicMock()
    result = MagicMock(
        data=[
            {"id": 1, "room_id": "room-1", "user_id": "u1", "joined_at": "t1", "left_at": None},
            {"id": 2, "room_id": "room-1", "user_id": "u2", "joined_at": "t2", "left_at": None},
        ]
    )
    client.table.return_value = _table_mock(result)
    roster = await fetch_active_roster(client, "room-1")
    assert roster == [
        Member(id=1, room_id="room-1", user_id="u1", joined_at="t1", left_at=None),
        Member(id=2, room_id="room-1", user_id="u2", joined_at="t2", left_at=None),
    ]
