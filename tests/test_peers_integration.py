# tests/test_peers_integration.py
"""Connects two real PeerConnectionManager instances to each other in-process over
loopback host candidates (no STUN needed on one machine) — proves offer/answer/ICE
actually establishes a working RTCPeerConnection and routes media frames.
"""

import asyncio

import numpy as np
import pytest

from termcall.media import LocalVideoTrack
from termcall.peers import PeerConnectionManager


@pytest.mark.timeout(30)
async def test_two_peers_connect_and_exchange_video():
    a_frames: list[np.ndarray] = []
    b_frames: list[np.ndarray] = []
    a_states: list[str] = []
    b_states: list[str] = []

    manager_a = PeerConnectionManager(
        on_video_frame=lambda peer_id, frame: a_frames.append(frame),
        on_audio_frame=lambda peer_id, frame: None,
        on_state_change=lambda peer_id, state: a_states.append(state),
        ice_servers=[],
    )
    manager_b = PeerConnectionManager(
        on_video_frame=lambda peer_id, frame: b_frames.append(frame),
        on_audio_frame=lambda peer_id, frame: None,
        on_state_change=lambda peer_id, state: b_states.append(state),
        ice_servers=[],
    )

    track_a = LocalVideoTrack(frame_source=lambda: np.full((4, 4, 3), 42, dtype=np.uint8), fps=30.0)
    track_b = LocalVideoTrack(frame_source=lambda: np.full((4, 4, 3), 84, dtype=np.uint8), fps=30.0)

    offer = await manager_a.create_offer("b", [track_a])
    answer = await manager_b.accept_offer("a", offer.sdp, [track_b])
    await manager_a.accept_answer("b", answer.sdp)

    for _ in range(200):
        if "connected" in a_states and "connected" in b_states:
            break
        await asyncio.sleep(0.05)

    assert "connected" in a_states
    assert "connected" in b_states

    for _ in range(200):
        if a_frames and b_frames:
            break
        await asyncio.sleep(0.05)

    # VP8 is a lossy codec: even a solid-color frame round-trips through
    # RGB->YUV420->quantized-DCT-encode->decode->RGB with a few units of
    # deterministic quantization drift, so exact-equality is not realistic here.
    assert a_frames and np.abs(a_frames[0].astype(int) - 84).max() <= 8  # A received B's frames
    assert b_frames and np.abs(b_frames[0].astype(int) - 42).max() <= 8  # B received A's frames

    await manager_a.close_all()
    await manager_b.close_all()
