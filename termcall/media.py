# termcall/media.py
"""aiortc media tracks wrapping the local camera and microphone (design §7)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from fractions import Fraction

import av
import cv2
import numpy as np
import sounddevice as sd
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError, VideoStreamTrack

VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = Fraction(1, VIDEO_CLOCK_RATE)


class DeviceError(Exception):
    """Raised when the camera or microphone cannot be opened."""


class LocalVideoTrack(VideoStreamTrack):
    """Wraps a blocking frame-source callable (e.g. `cv2.VideoCapture.read`'s second
    return value) as an aiortc VideoStreamTrack, running the blocking read in the
    event loop's default executor and pacing frames at `fps`.
    """

    def __init__(self, frame_source: Callable[[], np.ndarray], fps: float) -> None:
        super().__init__()
        self._frame_source = frame_source
        self._frame_interval = 1.0 / fps
        self._start: float | None = None
        self._frame_count = 0
        self.enabled = True

    async def _next_timestamp(self) -> tuple[int, Fraction]:
        if self._start is None:
            self._start = time.monotonic()
        else:
            self._frame_count += 1
            target = self._start + self._frame_count * self._frame_interval
            wait = target - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        pts = int(self._frame_count * self._frame_interval * VIDEO_CLOCK_RATE)
        return pts, VIDEO_TIME_BASE

    async def recv(self) -> av.VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError
        loop = asyncio.get_event_loop()
        bgr = await loop.run_in_executor(None, self._frame_source)
        if self.enabled:
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        else:
            rgb = np.zeros_like(bgr)
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        pts, time_base = await self._next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        return frame


AUDIO_SAMPLE_RATE = 48000
AUDIO_SAMPLES_PER_FRAME = 960  # 20ms at 48kHz, matching WebRTC's default Opus framing
AUDIO_TIME_BASE = Fraction(1, AUDIO_SAMPLE_RATE)


class LocalAudioTrack(AudioStreamTrack):
    """Wraps an asyncio.Queue of int16 mono PCM chunks (each AUDIO_SAMPLES_PER_FRAME
    samples), fed by a sounddevice.InputStream callback via `open_microphone`, as an
    aiortc AudioStreamTrack.
    """

    def __init__(self, queue: asyncio.Queue) -> None:
        super().__init__()
        self._queue = queue
        self._samples_sent = 0
        self.muted = False

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        chunk = await self._queue.get()
        if self.muted:
            chunk = np.zeros_like(chunk)
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = AUDIO_SAMPLE_RATE
        frame.pts = self._samples_sent
        frame.time_base = AUDIO_TIME_BASE
        self._samples_sent += chunk.shape[0]
        return frame


def open_camera(device: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        raise DeviceError(
            f"Could not open camera device {device}. Check the device index and that "
            "this terminal has camera permission."
        )
    return cap


def open_microphone(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> sd.InputStream:
    def _callback(indata, _frames, _time_info, _status) -> None:
        chunk = indata[:, 0].copy()
        loop.call_soon_threadsafe(queue.put_nowait, chunk)

    try:
        return sd.InputStream(
            samplerate=AUDIO_SAMPLE_RATE,
            blocksize=AUDIO_SAMPLES_PER_FRAME,
            channels=1,
            dtype="int16",
            callback=_callback,
        )
    except sd.PortAudioError as err:
        raise DeviceError(f"Could not open the microphone: {err}") from err
