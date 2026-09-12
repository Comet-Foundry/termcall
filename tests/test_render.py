import os

import numpy as np
import pytest

from termcall.render import frame_to_ansi, output_size, terminal_size


def test_frame_to_ansi_renders_top_and_bottom_pixel_colors():
    # 2x1 frame: top pixel red, bottom pixel green -> one output line.
    rgb = np.array([[[255, 0, 0]], [[0, 255, 0]]], dtype=np.uint8)
    ansi = frame_to_ansi(rgb)
    assert "38;2;255;0;0" in ansi
    assert "48;2;0;255;0" in ansi
    assert ansi.count("\n") == 0  # single output row for a 2-pixel-tall frame


def test_frame_to_ansi_emits_one_line_per_two_pixel_rows():
    rgb = np.zeros((4, 3, 3), dtype=np.uint8)
    ansi = frame_to_ansi(rgb)
    assert ansi.count("\n") == 1  # 4 pixel rows -> 2 text lines -> 1 newline


def test_terminal_size_falls_back_when_not_a_tty(monkeypatch):
    def raise_oserror(_fd):
        raise OSError("not a tty")

    monkeypatch.setattr("os.get_terminal_size", raise_oserror)
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback: os.terminal_size(fallback))
    assert terminal_size() == (80, 24)


def test_output_size_uses_target_width_when_given(monkeypatch):
    monkeypatch.setattr("termcall.render.terminal_size", lambda: (120, 40))
    assert output_size(50) == (50, 40)
    assert output_size(None) == (120, 40)
