# tests/test_media_devices.py
import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import sounddevice

from termcall.media import AUDIO_SAMPLE_RATE, AUDIO_SAMPLES_PER_FRAME, DeviceError, open_camera, open_microphone


def test_open_camera_raises_device_error_when_unopenable():
    fake_cap = MagicMock()
    fake_cap.isOpened.return_value = False
    with patch("termcall.media.cv2.VideoCapture", return_value=fake_cap):
        with pytest.raises(DeviceError, match="Could not open camera device 2"):
            open_camera(2)


def test_open_camera_returns_capture_when_opened():
    fake_cap = MagicMock()
    fake_cap.isOpened.return_value = True
    with patch("termcall.media.cv2.VideoCapture", return_value=fake_cap):
        assert open_camera(0) is fake_cap


def test_open_microphone_raises_device_error_on_port_audio_failure():
    loop = asyncio.new_event_loop()
    try:
        with patch(
            "termcall.media.sd.InputStream", side_effect=sounddevice.PortAudioError("no default input device")
        ):
            with pytest.raises(DeviceError, match="microphone"):
                open_microphone(asyncio.Queue(), loop)
    finally:
        loop.close()


def test_open_microphone_callback_pushes_chunks_onto_queue_thread_safely():
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    captured = {}

    def fake_input_stream(*, samplerate, blocksize, channels, dtype, callback, **kwargs):
        captured["callback"] = callback
        return MagicMock()

    try:
        with patch("termcall.media.sd.InputStream", side_effect=fake_input_stream):
            open_microphone(queue, loop)

        chunk = np.zeros((AUDIO_SAMPLES_PER_FRAME, 1), dtype=np.int16)
        captured["callback"](chunk, AUDIO_SAMPLES_PER_FRAME, None, None)
        # call_soon_threadsafe only *schedules* the put; the loop must actually run
        # once to drain it (mirrors how the real event loop drains it while idling).
        loop.run_until_complete(asyncio.sleep(0))
        assert queue.qsize() == 1
    finally:
        loop.close()
