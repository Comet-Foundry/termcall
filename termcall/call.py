# termcall/call.py
"""CallSession: ties room membership, signaling, peer connections, and rendering
together for `termcall room create`/`termcall room join` (design §6, §8, §9).
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable

import cv2
import numpy as np

from termcall.grid import compose_grid, grid_dimensions
from termcall.media import AudioMixer
from termcall.peers import PeerConnectionManager
from termcall.render import CURSOR_HOME, QUIT_KEYS, frame_to_ansi, live_screen, output_size, raw_terminal
from termcall.rooms import Member
from termcall.signaling import Signal, am_i_offerer, build_answer_payload, build_offer_payload

SendSignal = Callable[[str, str, dict], Awaitable[None]]
LeaveRoom = Callable[[], Awaitable[None]]


class CallSession:
    def __init__(
        self,
        *,
        my_user_id: str,
        my_member_id: int,
        local_video_track,
        local_audio_track,
        camera_frame_source: Callable[[], np.ndarray],
        send_signal: SendSignal,
        leave_room: LeaveRoom,
        target_width: int | None = None,
        frame_interval: float = 1.0 / 20.0,
    ) -> None:
        self.my_user_id = my_user_id
        self.my_member_id = my_member_id
        self.local_video_track = local_video_track
        self.local_audio_track = local_audio_track
        self.camera_frame_source = camera_frame_source
        self.send_signal = send_signal
        self.leave_room = leave_room
        self.target_width = target_width
        self.frame_interval = frame_interval

        self.roster: dict[str, Member] = {}
        self.video_tiles: dict[str, np.ndarray | None] = {}
        self.video_disabled = False
        self.failed_peers: set[str] = set()
        self.fatal_error: str | None = None
        self.left_room = False
        self._shutdown = asyncio.Event()

        self.peers = PeerConnectionManager(
            on_video_frame=self._on_video_frame,
            on_audio_frame=self._on_audio_frame,
            on_state_change=self._on_state_change,
        )
        self.audio_mixer = AudioMixer()

    def _local_tracks(self) -> list:
        return [self.local_video_track, self.local_audio_track]

    def _on_video_frame(self, peer_id: str, frame: np.ndarray) -> None:
        self.video_tiles[peer_id] = frame

    def _on_audio_frame(self, peer_id: str, frame: np.ndarray) -> None:
        self.audio_mixer.push(peer_id, frame)

    def _on_state_change(self, peer_id: str, state: str) -> None:
        """Design §10: a failed peer connection shows a persistent placeholder tile
        if other connections remain up; if it was our only peer, the call ends with a
        fatal, non-zero-exit error instead of silently hanging.
        """
        if state == "failed":
            self.failed_peers.add(peer_id)
            if self.roster and self.failed_peers >= set(self.roster):
                self.fatal_error = (
                    "Could not establish a direct connection with your peer "
                    "(NAT traversal failed, no relay server configured)"
                )
                self._shutdown.set()
        elif state == "connected":
            self.failed_peers.discard(peer_id)

    async def handle_member_joined(self, member: Member) -> None:
        if member.user_id == self.my_user_id:
            return
        self.roster[member.user_id] = member
        self.video_tiles.setdefault(member.user_id, None)
        if am_i_offerer(self.my_member_id, member.id):
            offer = await self.peers.create_offer(member.user_id, self._local_tracks())
            await self.send_signal(member.user_id, "offer", build_offer_payload(offer.sdp))

    async def handle_member_left(self, member: Member) -> None:
        self.roster.pop(member.user_id, None)
        self.video_tiles.pop(member.user_id, None)
        self.failed_peers.discard(member.user_id)
        self.audio_mixer.remove(member.user_id)
        await self.peers.close(member.user_id)

    async def handle_signal(self, signal: Signal) -> None:
        if signal.sender_id not in self.roster:
            return  # ignore signals from users who are not active members of this room
        if signal.kind == "offer":
            answer = await self.peers.accept_offer(signal.sender_id, signal.payload["sdp"], self._local_tracks())
            await self.send_signal(signal.sender_id, "answer", build_answer_payload(answer.sdp))
        elif signal.kind == "answer":
            await self.peers.accept_answer(signal.sender_id, signal.payload["sdp"])
        elif signal.kind == "ice":
            await self.peers.add_ice_candidate(signal.sender_id, signal.payload)

    def handle_key(self, key: str) -> None:
        if key in QUIT_KEYS:
            asyncio.ensure_future(self._leave())
        elif key == "m":
            self.local_audio_track.muted = not self.local_audio_track.muted
        elif key == "v":
            self.video_disabled = not self.video_disabled
            self.local_video_track.enabled = not self.video_disabled

    async def _leave(self) -> None:
        self._shutdown.set()
        await self.peers.close_all()
        await self.leave_room()
        self.left_room = True

    def install_keyboard_reader(self, loop: asyncio.AbstractEventLoop) -> None:
        loop.add_reader(sys.stdin.fileno(), self._on_stdin_readable)

    def remove_keyboard_reader(self, loop: asyncio.AbstractEventLoop) -> None:
        loop.remove_reader(sys.stdin.fileno())

    def _on_stdin_readable(self) -> None:
        char = sys.stdin.read(1)
        self.handle_key(char)

    def _ordered_tile_user_ids(self) -> list[str]:
        peers_by_join_order = sorted(self.roster, key=lambda uid: self.roster[uid].id)
        return ["__self__", *peers_by_join_order]

    def compose_frame(self, self_frame: np.ndarray | None) -> str:
        ordered = self._ordered_tile_user_ids()
        cols, rows = grid_dimensions(len(ordered))
        term_cols, term_rows = output_size(self.target_width)
        tile_w = max(1, term_cols // cols)
        tile_h = max(2, (term_rows // rows) * 2)

        FAILED_TILE_RGB = (139, 0, 0)  # dark red: visually distinct from a black "no frame yet" tile
        tiles = []
        for user_id in ordered:
            if user_id in self.failed_peers:
                pixels = np.tile(np.array(FAILED_TILE_RGB, dtype=np.uint8), (tile_h, tile_w, 1))
            else:
                frame = self_frame if user_id == "__self__" else self.video_tiles.get(user_id)
                if frame is None:
                    pixels = np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
                else:
                    pixels = cv2.resize(frame, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
                    if user_id == "__self__":
                        pixels = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
            tiles.append(frame_to_ansi(pixels))

        blank_tile = frame_to_ansi(np.zeros((tile_h, tile_w, 3), dtype=np.uint8))
        return compose_grid(tiles, cols, rows, blank_tile)

    def _capture_self_frame(self) -> np.ndarray:
        frame = self.camera_frame_source()
        if self.video_disabled:
            return np.zeros_like(frame)
        return frame

    async def render_loop(self) -> None:
        loop = asyncio.get_event_loop()
        with live_screen(), raw_terminal():
            while not self._shutdown.is_set():
                start = time.monotonic()
                self_frame = await loop.run_in_executor(None, self._capture_self_frame)
                sys.stdout.write(CURSOR_HOME + self.compose_frame(self_frame))
                sys.stdout.flush()
                elapsed = time.monotonic() - start
                await asyncio.sleep(max(self.frame_interval - elapsed, 0.0))

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        self.install_keyboard_reader(loop)
        try:
            await self.render_loop()
        finally:
            self.remove_keyboard_reader(loop)
