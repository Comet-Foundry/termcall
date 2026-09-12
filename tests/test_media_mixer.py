import numpy as np
import pytest
import sounddevice
from unittest.mock import patch

from termcall.media import AudioMixer, DeviceError, open_speaker


def test_pull_mixed_sums_two_peers_and_clips():
    mixer = AudioMixer()
    mixer.push("a", np.full(4, 20000, dtype=np.int16))
    mixer.push("b", np.full(4, 20000, dtype=np.int16))
    mixed = mixer.pull_mixed(4)
    assert (mixed == 32767).all()  # 40000 clipped to int16 max


def test_pull_mixed_returns_silence_when_no_peers_have_data():
    mixer = AudioMixer()
    mixed = mixer.pull_mixed(4)
    assert (mixed == 0).all()


def test_pull_mixed_consumes_one_chunk_per_call():
    mixer = AudioMixer()
    mixer.push("a", np.full(4, 100, dtype=np.int16))
    mixer.push("a", np.full(4, 200, dtype=np.int16))
    first = mixer.pull_mixed(4)
    second = mixer.pull_mixed(4)
    third = mixer.pull_mixed(4)
    assert (first == 100).all()
    assert (second == 200).all()
    assert (third == 0).all()  # queue drained, falls back to silence


def test_open_speaker_raises_device_error_on_port_audio_failure():
    with patch("termcall.media.sd.OutputStream", side_effect=sounddevice.PortAudioError("no default output device")):
        with pytest.raises(DeviceError, match="speaker"):
            open_speaker(AudioMixer())
