# tests/test_call_render.py
from unittest.mock import MagicMock

import numpy as np

from termcall.call import CallSession
from termcall.rooms import Member


def _session():
    return CallSession(
        my_user_id="me",
        my_member_id=1,
        local_video_track=MagicMock(),
        local_audio_track=MagicMock(),
        camera_frame_source=lambda: np.zeros((4, 4, 3), dtype=np.uint8),
        send_signal=None,
        leave_room=None,
        target_width=8,
    )


def test_compose_frame_with_only_self_is_a_single_tile():
    session = _session()
    frame = session.compose_frame(np.zeros((4, 4, 3), dtype=np.uint8))
    assert isinstance(frame, str)
    assert frame != ""


def test_compose_frame_orders_peers_by_join_order():
    session = _session()
    early = Member(id=2, room_id="r", user_id="early", joined_at="t", left_at=None)
    late = Member(id=3, room_id="r", user_id="late", joined_at="t", left_at=None)
    session.roster = {"late": late, "early": early}
    session.video_tiles = {"early": np.full((4, 4, 3), 10, dtype=np.uint8), "late": None}

    order = session._ordered_tile_user_ids()
    assert order == ["__self__", "early", "late"]


def test_compose_frame_uses_black_tile_for_missing_peer_frame():
    session = _session()
    peer = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.roster = {"peer-1": peer}
    session.video_tiles = {"peer-1": None}
    frame = session.compose_frame(np.zeros((4, 4, 3), dtype=np.uint8))
    assert isinstance(frame, str)
