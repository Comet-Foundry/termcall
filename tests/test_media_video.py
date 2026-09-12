# tests/test_media_video.py
import numpy as np
import pytest

from termcall.media import LocalVideoTrack


async def test_recv_converts_bgr_frame_to_rgb_video_frame():
    calls = {"n": 0}

    def frame_source():
        calls["n"] += 1
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        frame[:, :, 2] = 255  # BGR: pure blue channel set -> RGB red channel after conversion
        return frame

    track = LocalVideoTrack(frame_source=frame_source, fps=1000.0)
    frame = await track.recv()

    assert frame.width == 4
    assert frame.height == 4
    rgb = frame.to_ndarray(format="rgb24")
    assert rgb[0, 0, 0] == 255  # red channel, since source's blue channel was set
    assert rgb[0, 0, 2] == 0
    track.stop()


async def test_recv_pts_increases_across_frames():
    track = LocalVideoTrack(frame_source=lambda: np.zeros((2, 2, 3), dtype=np.uint8), fps=1000.0)
    frame1 = await track.recv()
    frame2 = await track.recv()
    assert frame2.pts > frame1.pts
    track.stop()


async def test_recv_returns_black_frame_when_disabled():
    track = LocalVideoTrack(frame_source=lambda: np.full((2, 2, 3), 200, dtype=np.uint8), fps=1000.0)
    track.enabled = False
    frame = await track.recv()
    rgb = frame.to_ndarray(format="rgb24")
    assert (rgb == 0).all()
    track.stop()
