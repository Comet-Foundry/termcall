# tests/test_call_keyboard.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

from termcall.call import CallSession


def _session():
    return CallSession(
        my_user_id="me",
        my_member_id=1,
        local_video_track=MagicMock(enabled=True),
        local_audio_track=MagicMock(muted=False),
        camera_frame_source=lambda: None,
        send_signal=AsyncMock(),
        leave_room=AsyncMock(),
    )


def test_m_key_toggles_audio_mute():
    session = _session()
    session.handle_key("m")
    assert session.local_audio_track.muted is True
    session.handle_key("m")
    assert session.local_audio_track.muted is False


def test_v_key_toggles_video_disabled_and_track_enabled():
    session = _session()
    session.handle_key("v")
    assert session.video_disabled is True
    assert session.local_video_track.enabled is False
    session.handle_key("v")
    assert session.video_disabled is False
    assert session.local_video_track.enabled is True


def test_other_keys_are_ignored():
    session = _session()
    session.handle_key("x")
    assert session.local_audio_track.muted is False
    assert session.video_disabled is False


async def test_leave_sets_shutdown_closes_peers_and_calls_leave_room():
    session = _session()
    session.peers = MagicMock()
    session.peers.close_all = AsyncMock()

    await session._leave()

    assert session._shutdown.is_set()
    session.peers.close_all.assert_awaited_once()
    session.leave_room.assert_awaited_once()
    assert session.left_room is True


async def test_quit_key_schedules_leave():
    session = _session()
    session.peers = MagicMock()
    session.peers.close_all = AsyncMock()
    session.handle_key("q")
    await asyncio.sleep(0)
    session.leave_room.assert_awaited_once()
