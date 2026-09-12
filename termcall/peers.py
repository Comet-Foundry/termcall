# termcall/peers.py
"""One RTCPeerConnection per peer, keyed by user_id, with MediaRelay fan-out so a
single local video/audio track can feed up to 3 simultaneous connections (design §7).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import numpy as np
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import candidate_from_sdp

logger = logging.getLogger(__name__)

ICE_SERVERS = [RTCIceServer(urls="stun:stun.l.google.com:19302")]

OnVideoFrame = Callable[[str, np.ndarray], None]
OnAudioFrame = Callable[[str, np.ndarray], None]
OnStateChange = Callable[[str, str], None]


class PeerConnectionManager:
    def __init__(
        self,
        *,
        on_video_frame: OnVideoFrame,
        on_audio_frame: OnAudioFrame,
        on_state_change: OnStateChange,
        ice_servers: list[RTCIceServer] | None = None,
    ) -> None:
        self._connections: dict[str, RTCPeerConnection] = {}
        self._relay = MediaRelay()
        self._on_video_frame = on_video_frame
        self._on_audio_frame = on_audio_frame
        self._on_state_change = on_state_change
        self._ice_servers = ICE_SERVERS if ice_servers is None else ice_servers

    async def _new_connection(self, peer_id: str) -> RTCPeerConnection:
        existing = self._connections.get(peer_id)
        if existing is not None:
            await existing.close()
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=self._ice_servers))
        self._connections[peer_id] = pc

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            self._on_state_change(peer_id, pc.connectionState)

        @pc.on("track")
        def on_track(track) -> None:
            asyncio.ensure_future(self._consume_track(peer_id, track))

        return pc

    async def _consume_track(self, peer_id: str, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                return
            except Exception:
                logger.exception("Error receiving %s frame from peer %s", track.kind, peer_id)
                return
            if track.kind == "video":
                self._on_video_frame(peer_id, frame.to_ndarray(format="rgb24"))
            else:
                self._on_audio_frame(peer_id, frame.to_ndarray().reshape(-1))

    async def _await_ice_gathering(self, pc: RTCPeerConnection) -> None:
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.05)

    async def create_offer(self, peer_id: str, local_tracks: list) -> RTCSessionDescription:
        pc = await self._new_connection(peer_id)
        for track in local_tracks:
            pc.addTrack(self._relay.subscribe(track))
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await self._await_ice_gathering(pc)
        return pc.localDescription

    async def accept_offer(self, peer_id: str, offer_sdp: str, local_tracks: list) -> RTCSessionDescription:
        pc = await self._new_connection(peer_id)
        for track in local_tracks:
            pc.addTrack(self._relay.subscribe(track))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._await_ice_gathering(pc)
        return pc.localDescription

    async def accept_answer(self, peer_id: str, answer_sdp: str) -> None:
        pc = self._connections[peer_id]
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))

    async def add_ice_candidate(self, peer_id: str, payload: dict) -> None:
        pc = self._connections.get(peer_id)
        if pc is None:
            return
        candidate = candidate_from_sdp(payload["candidate"].split(":", 1)[1])
        candidate.sdpMid = payload.get("sdpMid")
        candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
        await pc.addIceCandidate(candidate)

    async def close(self, peer_id: str) -> None:
        pc = self._connections.pop(peer_id, None)
        if pc is not None:
            await pc.close()

    async def close_all(self) -> None:
        await asyncio.gather(*(pc.close() for pc in self._connections.values()), return_exceptions=True)
        self._connections.clear()
