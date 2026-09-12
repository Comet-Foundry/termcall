# tests/test_media_audio.py
import asyncio

import numpy as np
import pytest

from termcall.media import AUDIO_SAMPLE_RATE, AUDIO_SAMPLES_PER_FRAME, LocalAudioTrack


async def test_recv_builds_audio_frame_from_queued_pcm_chunk():
    queue: asyncio.Queue = asyncio.Queue()
    chunk = np.full(AUDIO_SAMPLES_PER_FRAME, 1000, dtype=np.int16)
    queue.put_nowait(chunk)

    track = LocalAudioTrack(queue=queue)
    frame = await track.recv()

    assert frame.sample_rate == AUDIO_SAMPLE_RATE
    assert frame.samples == AUDIO_SAMPLES_PER_FRAME
    track.stop()


async def test_recv_pts_advances_by_samples_per_frame():
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(np.zeros(AUDIO_SAMPLES_PER_FRAME, dtype=np.int16))
    queue.put_nowait(np.zeros(AUDIO_SAMPLES_PER_FRAME, dtype=np.int16))

    track = LocalAudioTrack(queue=queue)
    frame1 = await track.recv()
    frame2 = await track.recv()
    assert frame2.pts - frame1.pts == AUDIO_SAMPLES_PER_FRAME
    track.stop()


async def test_recv_zeroes_samples_when_muted():
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(np.full(AUDIO_SAMPLES_PER_FRAME, 5000, dtype=np.int16))

    track = LocalAudioTrack(queue=queue)
    track.muted = True
    frame = await track.recv()

    samples = frame.to_ndarray()
    assert (samples == 0).all()
    track.stop()
