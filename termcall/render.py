"""Terminal rendering primitives: half-block ANSI frame rendering, alternate-screen
terminal management, and raw-mode keypress polling. Shared by `termcall preview` and
the in-call grid compositor (termcall/call.py).
"""

import contextlib
import os
import select
import shutil
import sys
import termios
import tty

import numpy as np

BLOCK = "\u2580"  # upper half block: fg paints the top pixel, bg paints the bottom pixel
RESET = "\x1b[0m"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"
CURSOR_HOME = "\x1b[H"
CLEAR_SCREEN = "\x1b[2J"
QUIT_KEYS = {"q", "Q", "\x1b", "\x03"}  # q, Esc, Ctrl-C


def frame_to_ansi(rgb: np.ndarray) -> str:
    """Render an (H, W, 3) uint8 RGB frame (H even) as one ANSI half-block string."""
    top = rgb[0::2]
    bottom = rgb[1::2]
    rows, cols, _ = top.shape
    lines = []
    for y in range(rows):
        top_row = top[y]
        bot_row = bottom[y]
        cells = [
            f"\x1b[38;2;{tr};{tg};{tb};48;2;{br};{bg};{bb}m{BLOCK}"
            for (tr, tg, tb), (br, bg, bb) in zip(top_row.tolist(), bot_row.tolist())
        ]
        lines.append("".join(cells))
    return RESET + (RESET + "\n").join(lines) + RESET


def terminal_size() -> tuple[int, int]:
    """Return (columns, lines) of the controlling terminal, with a safe fallback."""
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
        return size.columns, size.lines
    except OSError:
        size = shutil.get_terminal_size(fallback=(80, 24))
        return size.columns, size.lines


def output_size(target_width: int | None) -> tuple[int, int]:
    """Compute (cols, rows) of terminal cells to fill, rows always even in pixels."""
    cols, lines = terminal_size()
    if target_width is not None:
        cols = max(1, target_width)
    rows = max(1, lines)
    return cols, rows


@contextlib.contextmanager
def raw_terminal():
    """Put stdin in cbreak mode so single keypresses (q, Esc) are readable without Enter."""
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def quit_requested(timeout: float) -> bool:
    """Poll stdin for up to `timeout` seconds; return True if a quit key was pressed."""
    if timeout > 0:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return False
    else:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return False
    char = sys.stdin.read(1)
    return char in QUIT_KEYS


@contextlib.contextmanager
def live_screen():
    """Enter an alternate screen with a hidden cursor, restoring the terminal on exit."""
    sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR_SCREEN)
    sys.stdout.flush()
    try:
        yield
    finally:
        sys.stdout.write(RESET + SHOW_CURSOR + ALT_SCREEN_OFF)
        sys.stdout.flush()
