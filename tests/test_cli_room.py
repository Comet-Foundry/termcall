# tests/test_cli_room.py
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from main import cli
from termcall.media import DeviceError
from termcall.rooms import Room, RoomFullError, RoomNotJoinableError
from termcall.session import Session
from termcall.supabase_client import SessionExpiredError


def _patched_session():
    return patch(
        "termcall.supabase_client.authenticated_async_client",
        new=AsyncMock(return_value=(MagicMock(), Session("a", "r", "me@example.com"))),
    )


def test_room_join_reports_not_joinable():
    with _patched_session(), \
         patch("termcall.rooms.join_room", new=AsyncMock(side_effect=RoomNotJoinableError("room not joinable"))), \
         patch("main._resolve_user_id", new=AsyncMock(return_value="me")):
        result = CliRunner().invoke(cli, ["room", "join", "BADCODE"])
    assert result.exit_code != 0
    assert "no such room, or it has ended." in result.output


def test_room_join_reports_room_full():
    with _patched_session(), \
         patch("termcall.rooms.join_room", new=AsyncMock(side_effect=RoomFullError("room full"))), \
         patch("main._resolve_user_id", new=AsyncMock(return_value="me")):
        result = CliRunner().invoke(cli, ["room", "join", "FULLCODE"])
    assert result.exit_code != 0
    assert "room is full (max 4 participants)." in result.output


def test_room_create_reports_expired_session():
    with patch(
        "termcall.supabase_client.authenticated_async_client",
        new=AsyncMock(side_effect=SessionExpiredError("expired")),
    ):
        result = CliRunner().invoke(cli, ["room", "create"])
    assert result.exit_code != 0
    assert "termcall login" in result.output


def test_room_create_reports_camera_failure():
    room = Room(id="room-1", code="ABC123", host_id="me", status="open")
    with _patched_session(), \
         patch("termcall.rooms.create_room", new=AsyncMock(return_value=room)), \
         patch("main._resolve_user_id", new=AsyncMock(return_value="me")), \
         patch("termcall.media.open_camera", side_effect=DeviceError("Could not open camera device 0.")), \
         patch("termcall.rooms.leave_room", new=AsyncMock()):
        result = CliRunner().invoke(cli, ["room", "create"])
    assert result.exit_code != 0
    assert "Could not open camera device 0." in result.output


def test_room_create_exits_non_zero_when_call_session_reports_a_fatal_ice_failure():
    from termcall.rooms import Member

    room = Room(id="room-1", code="ABC123", host_id="me", status="open")
    fake_cap = MagicMock()
    fake_cap.read.return_value = (True, MagicMock())
    fake_stream = MagicMock()
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    fake_speaker = MagicMock()
    fake_speaker.__enter__ = MagicMock(return_value=fake_speaker)
    fake_speaker.__exit__ = MagicMock(return_value=False)
    fake_session = MagicMock()
    fake_session.run = AsyncMock()
    fake_session.fatal_error = (
        "Could not establish a direct connection with your peer "
        "(NAT traversal failed, no relay server configured)"
    )
    fake_channel = MagicMock()
    fake_channel.unsubscribe = AsyncMock()

    with _patched_session(), \
         patch("termcall.rooms.create_room", new=AsyncMock(return_value=room)), \
         patch("main._resolve_user_id", new=AsyncMock(return_value="me")), \
         patch("termcall.media.open_camera", return_value=fake_cap), \
         patch("termcall.media.open_microphone", return_value=fake_stream), \
         patch("termcall.media.open_speaker", return_value=fake_speaker), \
         patch(
             "termcall.rooms.fetch_active_roster",
             new=AsyncMock(return_value=[Member(id=1, room_id="room-1", user_id="me", joined_at="t", left_at=None)]),
         ), \
         patch(
             "termcall.signaling.subscribe_room_members", new=AsyncMock(return_value=([], fake_channel))
         ), \
         patch(
             "termcall.signaling.subscribe_signals", new=AsyncMock(return_value=([], fake_channel))
         ), \
         patch("termcall.rooms.leave_room", new=AsyncMock()), \
         patch("main.CallSession", return_value=fake_session):
        result = CliRunner().invoke(cli, ["room", "create"])

    assert result.exit_code != 0
    assert "NAT traversal failed" in result.output
