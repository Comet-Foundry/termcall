# tests/test_call_membership.py
from unittest.mock import AsyncMock, MagicMock

from termcall.call import CallSession
from termcall.rooms import Member


def _session(my_member_id=10):
    return CallSession(
        my_user_id="me",
        my_member_id=my_member_id,
        local_video_track=MagicMock(),
        local_audio_track=MagicMock(),
        camera_frame_source=lambda: None,
        send_signal=AsyncMock(),
        leave_room=AsyncMock(),
    )


async def test_ignores_own_membership_row():
    session = _session()
    await session.handle_member_joined(Member(id=10, room_id="r", user_id="me", joined_at="t", left_at=None))
    assert session.roster == {}


async def test_offers_to_a_later_joining_member():
    session = _session(my_member_id=1)
    session.peers = MagicMock()
    session.peers.create_offer = AsyncMock(return_value=MagicMock(sdp="offer-sdp"))

    peer = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    await session.handle_member_joined(peer)

    assert session.roster == {"peer-1": peer}
    session.peers.create_offer.assert_awaited_once_with("peer-1", [session.local_video_track, session.local_audio_track])
    session.send_signal.assert_awaited_once_with("peer-1", "offer", {"sdp": "offer-sdp"})


async def test_only_answers_an_earlier_joined_member_no_offer_sent():
    session = _session(my_member_id=5)
    session.peers = MagicMock()
    session.peers.create_offer = AsyncMock()

    earlier = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    await session.handle_member_joined(earlier)

    assert session.roster == {"peer-1": earlier}
    session.peers.create_offer.assert_not_awaited()
    session.send_signal.assert_not_awaited()


async def test_member_left_drops_roster_tile_and_closes_peer():
    session = _session(my_member_id=1)
    session.peers = MagicMock()
    session.peers.close = AsyncMock()
    peer = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.roster["peer-1"] = peer
    session.video_tiles["peer-1"] = "some-frame"

    await session.handle_member_left(peer)

    assert "peer-1" not in session.roster
    assert "peer-1" not in session.video_tiles
    session.peers.close.assert_awaited_once_with("peer-1")


async def test_handle_offer_signal_answers_and_sends_back():
    session = _session(my_member_id=5)
    from termcall.rooms import Member
    session.roster["peer-1"] = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.peers = MagicMock()
    session.peers.accept_offer = AsyncMock(return_value=MagicMock(sdp="answer-sdp"))

    from termcall.signaling import Signal

    signal = Signal(id=1, room_id="r", sender_id="peer-1", recipient_id="me", kind="offer", payload={"sdp": "offer-sdp"})
    await session.handle_signal(signal)

    session.peers.accept_offer.assert_awaited_once_with(
        "peer-1", "offer-sdp", [session.local_video_track, session.local_audio_track]
    )
    session.send_signal.assert_awaited_once_with("peer-1", "answer", {"sdp": "answer-sdp"})


async def test_handle_answer_signal_completes_negotiation():
    session = _session(my_member_id=1)
    from termcall.rooms import Member
    session.roster["peer-1"] = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.peers = MagicMock()
    session.peers.accept_answer = AsyncMock()

    from termcall.signaling import Signal

    signal = Signal(id=2, room_id="r", sender_id="peer-1", recipient_id="me", kind="answer", payload={"sdp": "answer-sdp"})
    await session.handle_signal(signal)

    session.peers.accept_answer.assert_awaited_once_with("peer-1", "answer-sdp")


async def test_handle_ice_signal_forwards_candidate():
    session = _session(my_member_id=1)
    from termcall.rooms import Member
    session.roster["peer-1"] = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.peers = MagicMock()
    session.peers.add_ice_candidate = AsyncMock()

    from termcall.signaling import Signal

    signal = Signal(
        id=3, room_id="r", sender_id="peer-1", recipient_id="me", kind="ice",
        payload={"candidate": "candidate:1 1 udp...", "sdpMid": "0", "sdpMLineIndex": 0},
    )
    await session.handle_signal(signal)

    session.peers.add_ice_candidate.assert_awaited_once_with("peer-1", signal.payload)


async def test_handle_signal_ignores_sender_not_in_roster():
    session = _session(my_member_id=1)
    session.peers = MagicMock()
    session.peers.accept_offer = AsyncMock()

    from termcall.signaling import Signal

    signal = Signal(id=1, room_id="r", sender_id="stranger", recipient_id="me", kind="offer", payload={"sdp": "x"})
    await session.handle_signal(signal)

    session.peers.accept_offer.assert_not_awaited()
    session.send_signal.assert_not_awaited()


async def test_state_change_to_failed_marks_peer_when_others_remain():
    session = _session(my_member_id=1)
    peer_a = Member(id=2, room_id="r", user_id="peer-a", joined_at="t", left_at=None)
    peer_b = Member(id=3, room_id="r", user_id="peer-b", joined_at="t", left_at=None)
    session.roster = {"peer-a": peer_a, "peer-b": peer_b}

    session._on_state_change("peer-a", "failed")

    assert "peer-a" in session.failed_peers
    assert session.fatal_error is None
    assert not session._shutdown.is_set()


async def test_state_change_to_failed_sets_fatal_error_when_it_was_the_only_peer():
    session = _session(my_member_id=1)
    peer = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.roster = {"peer-1": peer}

    session._on_state_change("peer-1", "failed")

    assert "peer-1" in session.failed_peers
    assert session.fatal_error == (
        "Could not establish a direct connection with your peer "
        "(NAT traversal failed, no relay server configured)"
    )
    assert session._shutdown.is_set()


async def test_state_change_to_connected_clears_failed_marker():
    session = _session(my_member_id=1)
    session.failed_peers.add("peer-1")
    session._on_state_change("peer-1", "connected")
    assert "peer-1" not in session.failed_peers
