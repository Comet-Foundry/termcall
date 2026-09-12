# tests/test_signaling.py
from unittest.mock import AsyncMock, MagicMock

import pytest

from termcall.signaling import (
    Signal,
    am_i_offerer,
    build_answer_payload,
    build_ice_payload,
    build_offer_payload,
    insert_signal,
    parse_signal,
)


@pytest.mark.parametrize(
    "my_id,peer_id,expected",
    [(1, 2, True), (2, 1, False), (5, 100, True), (100, 5, False)],
)
def test_am_i_offerer_lower_member_id_joined_earlier(my_id, peer_id, expected):
    assert am_i_offerer(my_id, peer_id) is expected


def test_am_i_offerer_rejects_self_comparison():
    with pytest.raises(ValueError):
        am_i_offerer(1, 1)


def test_payload_shapes():
    assert build_offer_payload("v=0...") == {"sdp": "v=0..."}
    assert build_answer_payload("v=0...") == {"sdp": "v=0..."}
    assert build_ice_payload("candidate:1 1 udp...", "0", 0) == {
        "candidate": "candidate:1 1 udp...",
        "sdpMid": "0",
        "sdpMLineIndex": 0,
    }


def test_parse_signal_round_trips_row_shape():
    row = {
        "id": 42,
        "room_id": "room-1",
        "sender_id": "u1",
        "recipient_id": "u2",
        "kind": "offer",
        "payload": {"sdp": "v=0..."},
    }
    assert parse_signal(row) == Signal(
        id=42, room_id="room-1", sender_id="u1", recipient_id="u2", kind="offer", payload={"sdp": "v=0..."}
    )


async def test_insert_signal_writes_expected_row():
    client = MagicMock()
    client.table.return_value.insert.return_value.execute = AsyncMock()
    await insert_signal(client, room_id="room-1", sender_id="u1", recipient_id="u2", kind="offer", payload={"sdp": "x"})
    client.table.assert_called_once_with("signals")
    client.table.return_value.insert.assert_called_once_with(
        {
            "room_id": "room-1",
            "sender_id": "u1",
            "recipient_id": "u2",
            "kind": "offer",
            "payload": {"sdp": "x"},
        }
    )
