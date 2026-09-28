"""Standalone driver for one participant of a real two-OS-process call, spawned by
tests/integration/test_two_process_call.py.

Everything downstream of media capture is the exact production call flow from
`main._run_call`: real Supabase auth, real `termcall.rooms`/`termcall.signaling`
(Postgres + Realtime), a real `termcall.call.CallSession`, and a real aiortc
`RTCPeerConnection` negotiated over genuine ICE. Only the camera/microphone are
replaced with synthetic sources, so the test is deterministic and does not fight
two processes over exclusive hardware access or trip macOS TCC permission prompts.

Usage (one process per participant):
    python -m tests.integration.call_peer_driver --role host
    python -m tests.integration.call_peer_driver --role join --code ABC123

Stdout contract:
    ROOM_CODE <code>   (host only, printed as soon as the room exists)
    RESULT <json>      (always the last line; see `run()` for the schema)
Exit code 0 iff the JSON result's "success" is true.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
import uuid

import numpy as np

from termcall import rooms, signaling, supabase_client
from termcall.call import CallSession
from termcall.media import AUDIO_SAMPLE_RATE, AUDIO_SAMPLES_PER_FRAME, LocalAudioTrack, LocalVideoTrack

# BGR tuples (matching what cv2.VideoCapture.read() would hand LocalVideoTrack), chosen
# so each side's synthetic frame is unambiguous after the sender's BGR->RGB conversion
# and the receiver's lossy VP8 decode: host sends red-dominant, join sends blue-dominant.
HOST_COLOR_BGR = (40, 40, 220)
JOIN_COLOR_BGR = (220, 40, 40)
FRAME_HEIGHT, FRAME_WIDTH = 240, 320
CONNECT_TIMEOUT_S = 30.0
FRAMES_REQUIRED = 5
SIGNUP_PASSWORD = "correct horse battery staple"  # ephemeral test account, value is irrelevant


class ObservedCallSession(CallSession):
    """CallSession subclass that records what a production session only reacts to,
    so the driver can assert on real connection state and real received media
    without reaching into private aiortc/mixer internals.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.video_frame_counts: dict[str, int] = {}
        self.audio_frame_counts: dict[str, int] = {}
        self.connection_states: dict[str, str] = {}
        # `connectionState` can flip to "closed"/"failed" the instant one side finishes
        # and tears down (see `run()`'s success-then-leave race); a peer that ever
        # reached "connected" proved the connection worked, regardless of what its
        # state reads at the moment we happen to poll it.
        self.ever_connected: set[str] = set()

    def _on_video_frame(self, peer_id: str, frame: np.ndarray) -> None:
        self.video_frame_counts[peer_id] = self.video_frame_counts.get(peer_id, 0) + 1
        super()._on_video_frame(peer_id, frame)

    def _on_audio_frame(self, peer_id: str, frame: np.ndarray) -> None:
        self.audio_frame_counts[peer_id] = self.audio_frame_counts.get(peer_id, 0) + 1
        super()._on_audio_frame(peer_id, frame)

    def _on_state_change(self, peer_id: str, state: str) -> None:
        self.connection_states[peer_id] = state
        if state == "connected":
            self.ever_connected.add(peer_id)
        super()._on_state_change(peer_id, state)


def make_video_source(color_bgr: tuple[int, int, int]):
    """A stand-in for `cap.read()[1]`: a solid-color frame in the given BGR color."""
    frame = np.empty((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    frame[:, :] = color_bgr

    def _source() -> np.ndarray:
        return frame

    return _source


async def feed_microphone(queue: asyncio.Queue, stop: asyncio.Event) -> None:
    """A stand-in for `open_microphone`'s sounddevice callback: pushes a 440Hz tone."""
    interval = AUDIO_SAMPLES_PER_FRAME / AUDIO_SAMPLE_RATE
    phase = 0
    while not stop.is_set():
        t = (np.arange(AUDIO_SAMPLES_PER_FRAME) + phase) / AUDIO_SAMPLE_RATE
        chunk = (np.sin(2 * np.pi * 440.0 * t) * 3000).astype(np.int16)
        phase += AUDIO_SAMPLES_PER_FRAME
        queue.put_nowait(chunk)
        await asyncio.sleep(interval)


async def run(role: str, code: str | None) -> dict:
    client = await supabase_client.build_async_client()
    email = f"{uuid.uuid4()}@example.test"
    signup_resp = await client.auth.sign_up({"email": email, "password": SIGNUP_PASSWORD})
    await client.auth.set_session(signup_resp.session.access_token, signup_resp.session.refresh_token)
    my_user_id = signup_resp.user.id

    if role == "host":
        room = await rooms.create_room(client, my_user_id)
        print(f"ROOM_CODE {room.code}", flush=True)
        my_color = HOST_COLOR_BGR
    else:
        room = await rooms.join_room(client, code)
        my_color = JOIN_COLOR_BGR

    roster = await rooms.fetch_active_roster(client, room.id)
    my_member = next(m for m in roster if m.user_id == my_user_id)

    mic_queue: asyncio.Queue = asyncio.Queue()
    stop_mic = asyncio.Event()
    mic_task = asyncio.ensure_future(feed_microphone(mic_queue, stop_mic))

    local_video = LocalVideoTrack(frame_source=make_video_source(my_color), fps=15.0)
    local_audio = LocalAudioTrack(queue=mic_queue)

    async def send_signal(recipient_id: str, kind: str, payload: dict) -> None:
        await signaling.insert_signal(
            client, room_id=room.id, sender_id=my_user_id, recipient_id=recipient_id, kind=kind, payload=payload
        )

    async def do_leave() -> None:
        await rooms.leave_room(client, room.id)

    session = ObservedCallSession(
        my_user_id=my_user_id,
        my_member_id=my_member.id,
        local_video_track=local_video,
        local_audio_track=local_audio,
        camera_frame_source=make_video_source(my_color),
        send_signal=send_signal,
        leave_room=do_leave,
    )

    result: dict = {
        "role": role,
        "user_id": my_user_id,
        "room_id": room.id,
        "room_code": room.code,
        "success": False,
    }

    members_channel = signals_channel = None
    try:
        for member in roster:
            if member.user_id != my_user_id:
                await session.handle_member_joined(member)

        members_backlog, members_channel = await signaling.subscribe_room_members(
            client, room.id, session.handle_member_joined
        )
        signals_backlog, signals_channel = await signaling.subscribe_signals(
            client, room.id, my_user_id, session.handle_signal
        )
        for member in members_backlog:
            if member.user_id != my_user_id and member.user_id not in session.roster:
                await session.handle_member_joined(member)
        for signal in signals_backlog:
            await session.handle_signal(signal)

        peer_id = None
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while time.monotonic() < deadline:
            if session.roster:
                peer_id = next(iter(session.roster))
                if (
                    peer_id in session.ever_connected
                    and session.video_frame_counts.get(peer_id, 0) >= FRAMES_REQUIRED
                    and session.audio_frame_counts.get(peer_id, 0) >= FRAMES_REQUIRED
                ):
                    break
            await asyncio.sleep(0.2)

        if peer_id is None:
            result["error"] = "peer never appeared in the roster"
        else:
            received_frame = session.video_tiles.get(peer_id)
            result.update(
                peer_id=peer_id,
                connected=peer_id in session.ever_connected,
                final_connection_state=session.connection_states.get(peer_id),
                video_frames_received=session.video_frame_counts.get(peer_id, 0),
                audio_frames_received=session.audio_frame_counts.get(peer_id, 0),
                received_mean_rgb=(None if received_frame is None else received_frame.reshape(-1, 3).mean(axis=0).tolist()),
            )
            result["success"] = (
                result["connected"]
                and result["video_frames_received"] >= FRAMES_REQUIRED
                and result["audio_frames_received"] >= FRAMES_REQUIRED
            )
    finally:
        stop_mic.set()
        mic_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await mic_task
        with contextlib.suppress(Exception):
            await session.peers.close_all()
        if members_channel is not None:
            with contextlib.suppress(Exception):
                await members_channel.unsubscribe()
        if signals_channel is not None:
            with contextlib.suppress(Exception):
                await signals_channel.unsubscribe()
        with contextlib.suppress(Exception):
            await do_leave()

    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["host", "join"], required=True)
    parser.add_argument("--code")
    args = parser.parse_args()

    try:
        result = asyncio.run(run(args.role, args.code))
    except Exception as exc:
        result = {"role": args.role, "success": False, "error": f"{type(exc).__name__}: {exc}"}

    print(f"RESULT {json.dumps(result)}", flush=True)
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
