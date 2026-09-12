from unittest.mock import AsyncMock, MagicMock, patch

from termcall.peers import PeerConnectionManager


def _manager():
    return PeerConnectionManager(
        on_video_frame=MagicMock(),
        on_audio_frame=MagicMock(),
        on_state_change=MagicMock(),
        ice_servers=[],
    )


async def test_create_offer_adds_relayed_tracks_and_waits_for_ice_complete():
    manager = _manager()
    fake_pc = MagicMock()
    fake_pc.createOffer = AsyncMock(return_value=MagicMock(sdp="offer-sdp"))
    fake_pc.setLocalDescription = AsyncMock()
    fake_pc.localDescription = MagicMock(sdp="offer-sdp")
    type(fake_pc).iceGatheringState = "complete"

    fake_relay = MagicMock()
    fake_relay.subscribe.return_value = "relayed-track"

    with patch("termcall.peers.RTCPeerConnection", return_value=fake_pc):
        manager._relay = fake_relay
        result = await manager.create_offer("peer-1", ["video-track", "audio-track"])

    assert fake_pc.addTrack.call_count == 2
    fake_relay.subscribe.assert_any_call("video-track")
    fake_relay.subscribe.assert_any_call("audio-track")
    assert result.sdp == "offer-sdp"


async def test_close_removes_and_closes_the_connection():
    manager = _manager()
    fake_pc = AsyncMock()
    manager._connections["peer-1"] = fake_pc
    await manager.close("peer-1")
    fake_pc.close.assert_awaited_once()
    assert "peer-1" not in manager._connections


async def test_close_all_closes_every_connection():
    manager = _manager()
    pc_a, pc_b = AsyncMock(), AsyncMock()
    manager._connections = {"a": pc_a, "b": pc_b}
    await manager.close_all()
    pc_a.close.assert_awaited_once()
    pc_b.close.assert_awaited_once()
    assert manager._connections == {}
