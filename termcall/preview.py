"""Local-only webcam-to-terminal preview: unchanged behavior from the pre-refactor
`main.py`. No auth/network dependency (spec §2).
"""

import sys
import time

import click
import cv2

from termcall.render import CURSOR_HOME, frame_to_ansi, live_screen, output_size, quit_requested, raw_terminal


def run(cap: cv2.VideoCapture, fps: float, target_width: int | None, mirror: bool) -> None:
    frame_interval = 1.0 / fps
    with live_screen(), raw_terminal():
        while True:
            start = time.monotonic()

            ok, frame = cap.read()
            if not ok:
                raise click.ClickException("Lost connection to the camera.")

            cols, rows = output_size(target_width)
            resized = cv2.resize(frame, (cols, rows * 2), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            if mirror:
                rgb = rgb[:, ::-1, :]

            sys.stdout.write(CURSOR_HOME + frame_to_ansi(rgb))
            sys.stdout.flush()

            elapsed = time.monotonic() - start
            remaining = frame_interval - elapsed
            if quit_requested(max(remaining, 0.0)):
                return


def preview(device: int, fps: float, target_width: int | None, mirror: bool) -> None:
    """Open the camera, run the live preview loop, and always release the camera."""
    if fps <= 0:
        raise click.BadParameter("must be greater than 0", param_hint="--fps")

    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        raise click.ClickException(
            f"Could not open camera device {device}. Check the device index and that "
            "this terminal has camera permission."
        )
    try:
        run(cap, fps, target_width, mirror)
    finally:
        cap.release()
