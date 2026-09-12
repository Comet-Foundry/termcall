
# P2P WebRTC Calling CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `termcall` from a local-only webcam preview into a Supabase-authenticated, room-code-based, up-to-4-participant P2P WebRTC mesh calling CLI, rendered as an n-way ANSI grid in the terminal, while keeping `termcall preview` working unchanged.

**Architecture:** A new `termcall/` package splits the monolithic `main.py` into focused modules — rendering primitives, session/auth, room/signaling data access, grid compositing, aiortc media tracks, peer-connection management, and call orchestration — wired together by a thin `click` group in `main.py`. Supabase Postgres + Realtime carries only signaling (SDP/ICE) and membership; all audio/video is direct `aiortc` `RTCPeerConnection` traffic between peers, one connection per pair, fanned out from a single local capture via `MediaRelay`.

**Tech Stack:** Python ≥3.13, `click`, `supabase` (sync `Client` for one-shot auth commands, async `AsyncClient` for the Realtime-dependent call flow), `aiortc` + `av`, `opencv-python-headless`, `sounddevice`, `keyring`, `pytest` + `pytest-asyncio` for tests, Supabase CLI (`supabase start`) for local RLS integration testing.

**Spec:** `docs/superpowers/specs/2026-09-11-p2p-webrtc-calling-design.md`

## Global Constraints

- Python `>=3.13` (existing `pyproject.toml` floor) — do not lower it.
- 4-participant room cap enforced server-side by `join_room`; client code must never assume more than 3 simultaneous peer connections.
- ICE is STUN-only: `RTCConfiguration(iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")])`. No TURN. This is a documented v1 limitation (spec §10), not a bug to work around.
- **Implementation-grounded deviation from spec §6's literal sequence diagram:** `aiortc` does not support trickle ICE — `RTCPeerConnection.setLocalDescription()` blocks (via polling `iceGatheringState`) until gathering completes, and all candidates are already embedded in the offer/answer SDP by the time it's sent (confirmed against `aiortc` issue #1344 / API docs, 2026-09-12). The `signals` table's `kind='ice'` path is implemented for schema completeness and inbound interop, but in normal operation between two `termcall` peers **zero `kind='ice'` rows will ever be inserted** — connectivity is established purely from the offer/answer exchange. Do not treat an absence of ICE signal rows as a bug.
- Grid layout is a pure function of participant count `n` (including self): `cols = ceil(sqrt(n))`, `rows = ceil(n / cols)`. For n=1..4 this is fixed: 1×1, 1×2, 2×2 (1 blank), 2×2.
- Offerer/answerer determination uses `room_members.id` (an `identity` column, monotonically increasing, never reused) as the join-order key — not `joined_at` timestamps, which can collide at sub-millisecond insert rates and aren't guaranteed unique. Lower `id` = joined earlier = offerer toward the higher-`id` peer. This is a strictly-equivalent, tie-break-free implementation of spec §6's "whoever joined earlier is the offerer."
- In-call keys: `q` / `Esc` / `Ctrl-C` leave (reuse existing `QUIT_KEYS` from `render.py`), `m` toggles outgoing audio mute, `v` toggles outgoing video. All three act uniformly across every peer connection via the shared local track (not per-peer).
- Session refresh policy (spec §10, implemented once in `supabase_client.py`): every authenticated command unconditionally calls `auth.refresh_session()` on load. Success rotates and re-persists tokens silently. Failure clears the keyring entry and raises `SessionExpiredError`, which every CLI command converts to `click.ClickException("Session expired. Run \`termcall login\` again.")`.
- Module import convention (required for mockability): command-bearing modules (`main.py`, `termcall/call.py`) import collaborator modules with `from termcall import auth, rooms, signaling, media, session as session_store, supabase_client` and call `module.function(...)` — never `from termcall.rooms import join_room`. This lets tests patch `termcall.rooms.join_room` and have every caller see the patch.
- Existing `termcall preview` behavior (frame algorithm, flags, quit keys) must be byte-for-byte unchanged after the refactor — it's a pure code move, not a rewrite (spec §13).
- New dependencies land in `pyproject.toml` exactly as named in spec §11: `supabase`, `aiortc`, `av`, `sounddevice`, `keyring`. `opencv-python-headless` and `numpy` are retained.
- Tests that need a running local Supabase instance (`supabase start`) are marked `@pytest.mark.integration` and skip themselves (not fail) when `SUPABASE_URL` isn't set — they are not part of the default `pytest` run.
- No project-wide `pytest`/lint run inside a task's steps beyond the test file(s) that task touches; a full-suite pass is Task 23's job.

---

## File Structure

| File | Responsibility |
|---|---|
| `main.py` | Thin `click` group: parses args/flags, converts domain exceptions to `click.ClickException`, delegates everything else to `termcall/*`. |
| `termcall/__init__.py` | Empty package marker. |
| `termcall/render.py` | Half-block ANSI frame rendering, terminal sizing, raw-mode/alt-screen context managers, quit-key polling. Moved verbatim from current `main.py`. |
| `termcall/preview.py` | The local-only `termcall preview` command body (webcam loop), unchanged behavior, now importing from `render.py`. |
| `termcall/session.py` | Keyring-backed `Session` (access/refresh token + email) read/write/clear. |
| `termcall/supabase_client.py` | Builds sync/async Supabase clients from env vars; enforces the refresh-on-load policy. |
| `termcall/auth.py` | `signup`/`login`/`logout`/`whoami` — sync client, no event loop needed. |
| `termcall/rooms.py` | Room code generation; `create_room`/`join_room`/`leave_room`/`fetch_active_roster` wrapping the `join_room`/`leave_room` RPCs; `Room`/`Member` dataclasses; error mapping. |
| `termcall/signaling.py` | `Signal` dataclass, offer/answer/ICE payload shaping/parsing, offerer/answerer determinism, Realtime backlog-then-stream subscriptions for `room_members` and `signals`. |
| `termcall/grid.py` | Pure grid-layout math (`grid_dimensions`) and the tile compositor (`compose_grid`). |
| `termcall/media.py` | `LocalVideoTrack`/`LocalAudioTrack` (aiortc track subclasses wrapping camera/mic), camera/mic device opening. |
| `termcall/peers.py` | `PeerConnectionManager` — one `RTCPeerConnection` per peer keyed by `user_id`, offer/answer/ICE handling, `MediaRelay` fan-out, connection-state monitoring. |
| `termcall/call.py` | `CallSession` orchestrator: membership-driven mesh formation, keyboard input, grid render loop; wires everything above together for `room create`/`room join`. |
| `supabase/migrations/0001_init_schema.sql` | `profiles`/`rooms`/`room_members`/`signals` tables + the `auth.users` provisioning trigger (spec §4, §4.1). |
| `supabase/migrations/0002_room_rpc.sql` | `join_room`/`leave_room` security-definer RPCs (spec §4.2). |
| `supabase/migrations/0003_rls_policies.sql` | RLS policies for all four tables (spec §4.3). |
| `tests/` | Unit tests mirroring the module list above; `tests/integration/test_rls.py` for the local-Supabase RLS suite. |

---

### Task 1: Extract rendering primitives into `termcall/render.py`

**Files:**
- Create: `termcall/__init__.py`
- Create: `termcall/render.py`
- Test: `tests/test_render.py`
- Modify: `main.py:1-127` (rendering primitives removed in Task 2 — this task only adds the new module; `main.py` keeps working unmodified until Task 2)

**Interfaces:**
- Produces: `BLOCK`, `RESET`, `HIDE_CURSOR`, `SHOW_CURSOR`, `ALT_SCREEN_ON`, `ALT_SCREEN_OFF`, `CURSOR_HOME`, `CLEAR_SCREEN`, `QUIT_KEYS` (constants); `frame_to_ansi(rgb: np.ndarray) -> str`; `terminal_size() -> tuple[int, int]`; `output_size(target_width: int | None) -> tuple[int, int]`; `raw_terminal()` (contextmanager); `quit_requested(timeout: float) -> bool`; `live_screen()` (contextmanager).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render.py
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
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback: type(
        "Size", (), {"columns": fallback[0], "lines": fallback[1]}
    )())
    assert terminal_size() == (80, 24)


def test_output_size_uses_target_width_when_given(monkeypatch):
    monkeypatch.setattr("termcall.render.terminal_size", lambda: (120, 40))
    assert output_size(50) == (50, 40)
    assert output_size(None) == (120, 40)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall'`

- [ ] **Step 3: Create the package and move the rendering code**

```python
# termcall/__init__.py
```

```python
# termcall/render.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/__init__.py termcall/render.py tests/test_render.py
git commit -m "feat: extract rendering primitives into termcall/render.py"
```

---

### Task 2: Move preview loop into `termcall/preview.py`; convert `main.py` to a `click` group

**Files:**
- Create: `termcall/preview.py`
- Modify: `main.py` (full rewrite from a single `@click.command()` to a `@click.group()` with a `preview` subcommand)
- Test: `tests/test_cli_preview.py`

**Interfaces:**
- Consumes: `termcall.render.{frame_to_ansi, live_screen, output_size, quit_requested, raw_terminal, CURSOR_HOME}` (Task 1).
- Produces: `termcall.preview.run(cap, fps, target_width, mirror) -> None`; `termcall.preview.preview(device, fps, target_width, mirror) -> None` (opens/releases the camera, raises `click.ClickException`/`click.BadParameter` on failure — used by `main.py`'s `preview` command). `main.py`'s `cli` group, callable as `python main.py <command>`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_preview.py
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from main import cli


def test_preview_help_lists_flags():
    result = CliRunner().invoke(cli, ["preview", "--help"])
    assert result.exit_code == 0
    assert "--fps" in result.output
    assert "--mirror" in result.output


def test_preview_rejects_non_positive_fps():
    result = CliRunner().invoke(cli, ["preview", "--fps", "0"])
    assert result.exit_code != 0
    assert "greater than 0" in result.output


@patch("termcall.preview.cv2.VideoCapture")
def test_preview_reports_unopenable_camera(mock_video_capture):
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    mock_video_capture.return_value = mock_cap

    result = CliRunner().invoke(cli, ["preview", "--device", "3"])
    assert result.exit_code != 0
    assert "Could not open camera device 3" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_preview.py -v`
Expected: FAIL with `ImportError: cannot import name 'cli' from 'main'`

- [ ] **Step 3: Write `termcall/preview.py` and rewrite `main.py`**

```python
# termcall/preview.py
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
```

```python
# main.py
"""termcall CLI entrypoint: a click group wiring auth, room, and preview commands."""

import click

from termcall import preview as preview_mod


@click.group()
def cli() -> None:
    """termcall: peer-to-peer terminal video calling."""


@cli.command("preview")
@click.option("-d", "--device", default=0, show_default=True, help="Camera device index.")
@click.option("--fps", default=20.0, show_default=True, help="Target frames per second.")
@click.option(
    "-w",
    "--width",
    "target_width",
    type=int,
    default=None,
    help="Output width in terminal columns (default: current terminal width).",
)
@click.option(
    "--mirror/--no-mirror",
    default=True,
    show_default=True,
    help="Mirror the image horizontally, like a selfie camera.",
)
def preview_cmd(device: int, fps: float, target_width: int | None, mirror: bool) -> None:
    """Show a live webcam feed as colored Unicode blocks in the terminal.

    Press q, Esc, or Ctrl-C to quit.
    """
    preview_mod.preview(device, fps, target_width, mirror)


if __name__ == "__main__":
    cli()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_preview.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/preview.py main.py tests/test_cli_preview.py
git commit -m "refactor: move preview loop to termcall/preview.py, main.py becomes a click group"
```

---

### Task 3: Add new dependencies and pytest tooling

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `pytest`/`pytest-asyncio` available via `uv run pytest`; `asyncio_mode = "auto"` so `async def test_...` functions run without per-test decorators.

- [ ] **Step 1: Edit `pyproject.toml`**

```toml
[project]
name = "termcall"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "click>=8.5.0",
    "numpy>=2.5.3",
    "opencv-python-headless>=5.0.0.93",
    "supabase>=2.10.0",
    "aiortc>=1.9.0",
    "av>=13.0.0",
    "sounddevice>=0.5.0",
    "keyring>=25.0.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "integration: requires a local Supabase instance (supabase start)",
]
```

- [ ] **Step 2: Sync the environment**

Run: `uv sync`
Expected: exits 0; `uv.lock` updates to include `supabase`, `aiortc`, `av`, `sounddevice`, `keyring`, `pytest`, `pytest-asyncio` and their transitive dependencies.

- [ ] **Step 3: Verify existing tests still collect under the new pytest config**

Run: `uv run pytest -v`
Expected: PASS (the 7 tests from Tasks 1–2, no others exist yet)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add supabase/aiortc/av/sounddevice/keyring deps and pytest tooling"
```

---

### Task 4: Core schema migration (`profiles`, `rooms`, `room_members`, `signals`)

**Files:**
- Create: `supabase/migrations/0001_init_schema.sql`

**Interfaces:**
- Produces: tables `profiles`, `rooms`, `room_members`, `signals` and the `auth.users` provisioning trigger, exactly as specified in design §4/§4.1. Later tasks' RPCs and RLS policies depend on these table/column names verbatim.

- [ ] **Step 1: Write the migration**

```sql
-- supabase/migrations/0001_init_schema.sql
create table profiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  email      text unique not null,
  created_at timestamptz not null default now()
);

create table rooms (
  id          uuid primary key default gen_random_uuid(),
  code        text unique not null,
  host_id     uuid not null references profiles(id),
  status      text not null default 'open'
              check (status in ('open', 'ended')),
  created_at  timestamptz not null default now(),
  ended_at    timestamptz
);

create table room_members (
  id         bigint generated always as identity primary key,
  room_id    uuid not null references rooms(id) on delete cascade,
  user_id    uuid not null references profiles(id),
  joined_at  timestamptz not null default now(),
  left_at    timestamptz
);

create table signals (
  id           bigint generated always as identity primary key,
  room_id      uuid not null references rooms(id) on delete cascade,
  sender_id    uuid not null references profiles(id),
  recipient_id uuid not null references profiles(id),
  kind         text not null check (kind in ('offer', 'answer', 'ice')),
  payload      jsonb not null,
  created_at   timestamptz not null default now()
);

create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email);
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();
```

- [ ] **Step 2: Apply it against a local instance and verify the shape**

Run: `supabase start` (first time only), then `supabase db reset`
Expected: migration applies with no errors; `supabase db reset` output includes `Applying migration 0001_init_schema.sql...` with no error lines.

Run: `supabase db execute --query "select table_name from information_schema.tables where table_schema = 'public' order by 1;"` (or `psql` equivalent from `supabase status`'s DB URL)
Expected: output includes `profiles`, `rooms`, `room_members`, `signals`.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/0001_init_schema.sql
git commit -m "feat(db): add profiles/rooms/room_members/signals schema + user-provisioning trigger"
```

---

### Task 5: RPC functions (`join_room`, `leave_room`)

**Files:**
- Create: `supabase/migrations/0002_room_rpc.sql`

**Interfaces:**
- Consumes: tables from Task 4.
- Produces: `join_room(p_code text) returns rooms`, `leave_room(p_room_id uuid) returns void` — the exact RPC names/signatures `termcall/rooms.py` (Task 11) calls via `client.rpc(...)`.

- [ ] **Step 1: Write the migration**

```sql
-- supabase/migrations/0002_room_rpc.sql
create function join_room(p_code text) returns rooms
language plpgsql security definer as $$
declare r rooms;
declare active_count int;
begin
  select * into r from rooms where code = p_code and status = 'open' for update;
  if r.id is null then
    raise exception 'room not joinable';
  end if;

  if exists (
    select 1 from room_members
    where room_id = r.id and user_id = auth.uid() and left_at is null
  ) then
    return r; -- already an active member; idempotent
  end if;

  select count(*) into active_count from room_members
    where room_id = r.id and left_at is null;
  if active_count >= 4 then
    raise exception 'room full';
  end if;

  insert into room_members (room_id, user_id) values (r.id, auth.uid());
  return r;
end;
$$;

create function leave_room(p_room_id uuid) returns void
language plpgsql security definer as $$
declare active_count int;
begin
  update room_members set left_at = now()
    where room_id = p_room_id and user_id = auth.uid() and left_at is null;

  select count(*) into active_count from room_members
    where room_id = p_room_id and left_at is null;
  if active_count = 0 then
    update rooms set status = 'ended', ended_at = now() where id = p_room_id;
  end if;
end;
$$;
```

- [ ] **Step 2: Apply and smoke-test the cap and idempotency behavior directly in SQL**

Run: `supabase db reset`
Expected: applies cleanly.

Run (via `supabase db execute` or `psql`, as the `postgres` role, simulating `auth.uid()` with `set local role authenticated; set local "request.jwt.claims" = '{"sub":"<uuid>"}';` per Supabase's local testing convention — or defer full behavioral proof to Task 7's integration suite, which exercises this through real authenticated clients):
```sql
select proname from pg_proc where proname in ('join_room', 'leave_room');
```
Expected: both function names present.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/0002_room_rpc.sql
git commit -m "feat(db): add join_room/leave_room security-definer RPCs with 4-cap enforcement"
```

---

### Task 6: RLS policies

**Files:**
- Create: `supabase/migrations/0003_rls_policies.sql`

**Interfaces:**
- Consumes: tables from Task 4.
- Produces: row-level security enabled and policies in place per design §4.3; Task 7's integration suite is the behavioral proof.

- [ ] **Step 1: Write the migration**

```sql
-- supabase/migrations/0003_rls_policies.sql
alter table profiles enable row level security;
alter table rooms enable row level security;
alter table room_members enable row level security;
alter table signals enable row level security;

create policy "profiles readable by any authenticated user"
  on profiles for select
  to authenticated
  using (true);

create policy "rooms selectable by active or past members"
  on rooms for select
  to authenticated
  using (
    exists (
      select 1 from room_members
      where room_members.room_id = rooms.id
        and room_members.user_id = auth.uid()
    )
  );

create policy "rooms insertable by their host while open"
  on rooms for insert
  to authenticated
  with check (host_id = auth.uid() and status = 'open');

create policy "room_members selectable by active members of the same room"
  on room_members for select
  to authenticated
  using (
    exists (
      select 1 from room_members as active
      where active.room_id = room_members.room_id
        and active.user_id = auth.uid()
        and active.left_at is null
    )
  );

create policy "signals selectable by sender or recipient"
  on signals for select
  to authenticated
  using (auth.uid() in (sender_id, recipient_id));

create policy "signals insertable by sender or recipient"
  on signals for insert
  to authenticated
  with check (auth.uid() in (sender_id, recipient_id));
```

- [ ] **Step 2: Apply**

Run: `supabase db reset`
Expected: applies cleanly with no errors.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/0003_rls_policies.sql
git commit -m "feat(db): add RLS policies for profiles/rooms/room_members/signals"
```

---

### Task 7: RLS integration test suite

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_rls.py`

**Interfaces:**
- Consumes: a running local Supabase instance (`supabase start`), `SUPABASE_URL`/`SUPABASE_ANON_KEY` env vars pointing at it, and `supabase.create_client`.
- Produces: automated proof of the four RLS/RPC guarantees called out in spec §12.

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/__init__.py
```

```python
# tests/integration/test_rls.py
"""Integration tests against a local Supabase instance (`supabase start`). Skipped
automatically unless SUPABASE_URL is set — these are not part of the default
`pytest` run (design §12).
"""

import os
import uuid

import pytest
from postgrest.exceptions import APIError
from supabase import Client, create_client

pytestmark = pytest.mark.integration

if not os.environ.get("SUPABASE_URL"):
    pytest.skip("SUPABASE_URL not set; run `supabase start` for RLS integration tests", allow_module_level=True)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]


def _signed_up_client() -> tuple[Client, str]:
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    email = f"{uuid.uuid4()}@example.test"
    password = "correct horse battery staple"
    response = client.auth.sign_up({"email": email, "password": password})
    client.auth.set_session(response.session.access_token, response.session.refresh_token)
    return client, response.user.id


@pytest.fixture
def users() -> list[tuple[Client, str]]:
    return [_signed_up_client() for _ in range(5)]


def test_fifth_join_to_a_full_room_is_rejected(users):
    host, _host_id = users[0]
    room = host.table("rooms").insert({"code": str(uuid.uuid4())[:8], "host_id": _host_id}).execute()
    code = room.data[0]["code"]
    host.rpc("join_room", {"p_code": code}).execute()

    for member, _uid in users[1:4]:
        member.rpc("join_room", {"p_code": code}).execute()

    fifth, _fifth_id = users[4]
    with pytest.raises(APIError, match="room full"):
        fifth.rpc("join_room", {"p_code": code}).execute()


def test_signals_are_not_readable_by_an_uninvolved_user(users):
    host, host_id = users[0]
    sender, sender_id = users[1]
    recipient, recipient_id = users[2]
    bystander, _bystander_id = users[3]

    room = host.table("rooms").insert({"code": str(uuid.uuid4())[:8], "host_id": host_id}).execute()
    room_id = room.data[0]["id"]
    host.rpc("join_room", {"p_code": room.data[0]["code"]}).execute()

    sender.table("signals").insert(
        {
            "room_id": room_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "kind": "offer",
            "payload": {"sdp": "v=0"},
        }
    ).execute()

    visible_to_recipient = recipient.table("signals").select("*").eq("sender_id", sender_id).execute()
    assert len(visible_to_recipient.data) == 1

    visible_to_bystander = bystander.table("signals").select("*").eq("sender_id", sender_id).execute()
    assert len(visible_to_bystander.data) == 0


def test_room_members_visible_only_to_active_members_of_that_room(users):
    host, host_id = users[0]
    outsider, _outsider_id = users[1]
    room = host.table("rooms").insert({"code": str(uuid.uuid4())[:8], "host_id": host_id}).execute()
    room_id = room.data[0]["id"]
    host.rpc("join_room", {"p_code": room.data[0]["code"]}).execute()

    visible_to_host = host.table("room_members").select("*").eq("room_id", room_id).execute()
    assert len(visible_to_host.data) == 1

    visible_to_outsider = outsider.table("room_members").select("*").eq("room_id", room_id).execute()
    assert len(visible_to_outsider.data) == 0
```

- [ ] **Step 2: Run test to verify it fails without a local instance (skip, not fail)**

Run: `uv run pytest tests/integration/test_rls.py -v`
Expected: `SKIPPED (SUPABASE_URL not set...)`, exit code 0.

- [ ] **Step 3: Start a local Supabase instance and run for real**

Run: `supabase start` then `export SUPABASE_URL=$(supabase status -o env | grep API_URL | cut -d= -f2) SUPABASE_ANON_KEY=$(supabase status -o env | grep ANON_KEY | cut -d= -f2)` then `uv run pytest tests/integration/test_rls.py -v -m integration`
Expected: PASS (3 passed)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_rls.py
git commit -m "test: add RLS integration suite against local Supabase"
```

---

### Task 8: `termcall/session.py` — keyring session storage

**Files:**
- Create: `termcall/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Produces: `Session(access_token: str, refresh_token: str, email: str)` (frozen dataclass); `save_session(session: Session) -> None`; `load_session() -> Session | None`; `clear_session() -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session.py
from unittest.mock import patch

import keyring.errors

from termcall.session import Session, clear_session, load_session, save_session


def test_round_trip_save_then_load():
    store: dict[str, str] = {}
    with patch("termcall.session.keyring.set_password", lambda service, user, value: store.__setitem__((service, user), value)), \
         patch("termcall.session.keyring.get_password", lambda service, user: store.get((service, user))):
        session = Session(access_token="a", refresh_token="r", email="me@example.com")
        save_session(session)
        assert load_session() == session


def test_load_returns_none_when_nothing_stored():
    with patch("termcall.session.keyring.get_password", return_value=None):
        assert load_session() is None


def test_clear_session_swallows_missing_entry():
    with patch("termcall.session.keyring.delete_password", side_effect=keyring.errors.PasswordDeleteError):
        clear_session()  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.session'`

- [ ] **Step 3: Write the implementation**

```python
# termcall/session.py
"""OS-keyring-backed session storage. Tokens are opaque strings we never log; only
the email is safe to print (`termcall whoami`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import keyring
import keyring.errors

KEYRING_SERVICE = "termcall"
KEYRING_USERNAME = "session"


@dataclass(frozen=True)
class Session:
    access_token: str
    refresh_token: str
    email: str


def save_session(session: Session) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, json.dumps(asdict(session)))


def load_session() -> Session | None:
    raw = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    if raw is None:
        return None
    return Session(**json.loads(raw))


def clear_session() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/session.py tests/test_session.py
git commit -m "feat: add keyring-backed session storage"
```

---

### Task 9: `termcall/supabase_client.py` — client builders + refresh policy

**Files:**
- Create: `termcall/supabase_client.py`
- Test: `tests/test_supabase_client.py`

**Interfaces:**
- Consumes: `termcall.session.{Session, load_session, save_session, clear_session}` (Task 8).
- Produces: `SessionExpiredError(Exception)`; `build_sync_client() -> Client`; `async def build_async_client() -> AsyncClient`; `authenticated_sync_client() -> tuple[Client, Session]`; `async def authenticated_async_client() -> tuple[AsyncClient, Session]`. Both `authenticated_*` functions apply the Global Constraints refresh policy and are what every later authenticated command calls.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_supabase_client.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gotrue.errors import AuthApiError

from termcall.session import Session
from termcall.supabase_client import SessionExpiredError, authenticated_async_client, authenticated_sync_client


def _fake_refresh_response(access="new-access", refresh="new-refresh"):
    resp = MagicMock()
    resp.session.access_token = access
    resp.session.refresh_token = refresh
    return resp


def test_authenticated_sync_client_refreshes_and_persists(monkeypatch):
    stored = Session(access_token="old", refresh_token="old-refresh", email="me@example.com")
    monkeypatch.setattr("termcall.supabase_client.load_session", lambda: stored)
    saved = {}
    monkeypatch.setattr("termcall.supabase_client.save_session", lambda s: saved.update(session=s))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    fake_client = MagicMock()
    fake_client.auth.refresh_session.return_value = _fake_refresh_response()
    with patch("termcall.supabase_client.create_client", return_value=fake_client):
        client, session = authenticated_sync_client()

    assert client is fake_client
    assert session.access_token == "new-access"
    assert session.email == "me@example.com"
    assert saved["session"].access_token == "new-access"
    fake_client.auth.refresh_session.assert_called_once_with("old-refresh")


def test_authenticated_sync_client_clears_session_on_invalid_refresh_token(monkeypatch):
    stored = Session(access_token="old", refresh_token="bad", email="me@example.com")
    monkeypatch.setattr("termcall.supabase_client.load_session", lambda: stored)
    cleared = {"called": False}
    monkeypatch.setattr("termcall.supabase_client.clear_session", lambda: cleared.update(called=True))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    fake_client = MagicMock()
    fake_client.auth.refresh_session.side_effect = AuthApiError("invalid refresh token", 401, "invalid_grant")
    with patch("termcall.supabase_client.create_client", return_value=fake_client):
        with pytest.raises(SessionExpiredError):
            authenticated_sync_client()

    assert cleared["called"] is True


def test_authenticated_sync_client_raises_when_nothing_stored(monkeypatch):
    monkeypatch.setattr("termcall.supabase_client.load_session", lambda: None)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    with pytest.raises(SessionExpiredError):
        authenticated_sync_client()


async def test_authenticated_async_client_refreshes_and_persists(monkeypatch):
    stored = Session(access_token="old", refresh_token="old-refresh", email="me@example.com")
    monkeypatch.setattr("termcall.supabase_client.load_session", lambda: stored)
    saved = {}
    monkeypatch.setattr("termcall.supabase_client.save_session", lambda s: saved.update(session=s))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    fake_client = MagicMock()
    fake_client.auth.set_session = AsyncMock()
    fake_client.auth.refresh_session = AsyncMock(return_value=_fake_refresh_response())
    with patch("termcall.supabase_client.acreate_client", new=AsyncMock(return_value=fake_client)):
        client, session = await authenticated_async_client()

    assert client is fake_client
    assert session.access_token == "new-access"
    assert saved["session"].access_token == "new-access"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_supabase_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.supabase_client'`

- [ ] **Step 3: Write the implementation**

```python
# termcall/supabase_client.py
"""Builds Supabase clients — sync for one-shot auth commands, async for the
Realtime-dependent call flow — and enforces the session refresh policy: every
authenticated command refreshes proactively on load; an invalid refresh token clears
the keyring entry so the CLI can prompt `termcall login` (design §10).
"""

from __future__ import annotations

import os

import click
from gotrue.errors import AuthApiError
from supabase import AsyncClient, Client, acreate_client, create_client

from termcall.session import Session, clear_session, load_session, save_session

SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_ANON_KEY_ENV = "SUPABASE_ANON_KEY"


class SessionExpiredError(Exception):
    """Raised when there is no stored session, or the refresh token is no longer valid."""


def _read_env() -> tuple[str, str]:
    url = os.environ.get(SUPABASE_URL_ENV)
    key = os.environ.get(SUPABASE_ANON_KEY_ENV)
    if not url or not key:
        raise click.ClickException(f"{SUPABASE_URL_ENV} and {SUPABASE_ANON_KEY_ENV} must be set.")
    return url, key


def build_sync_client() -> Client:
    url, key = _read_env()
    return create_client(url, key)


async def build_async_client() -> AsyncClient:
    url, key = _read_env()
    return await acreate_client(url, key)


def _rotate(session: Session, response) -> Session:
    return Session(
        access_token=response.session.access_token,
        refresh_token=response.session.refresh_token,
        email=session.email,
    )


def authenticated_sync_client() -> tuple[Client, Session]:
    session = load_session()
    if session is None:
        raise SessionExpiredError("no stored session")
    client = build_sync_client()
    client.auth.set_session(session.access_token, session.refresh_token)
    try:
        response = client.auth.refresh_session(session.refresh_token)
    except AuthApiError as err:
        clear_session()
        raise SessionExpiredError(str(err)) from err
    refreshed = _rotate(session, response)
    save_session(refreshed)
    client.auth.set_session(refreshed.access_token, refreshed.refresh_token)
    return client, refreshed


async def authenticated_async_client() -> tuple[AsyncClient, Session]:
    session = load_session()
    if session is None:
        raise SessionExpiredError("no stored session")
    client = await build_async_client()
    await client.auth.set_session(session.access_token, session.refresh_token)
    try:
        response = await client.auth.refresh_session(session.refresh_token)
    except AuthApiError as err:
        clear_session()
        raise SessionExpiredError(str(err)) from err
    refreshed = _rotate(session, response)
    save_session(refreshed)
    await client.auth.set_session(refreshed.access_token, refreshed.refresh_token)
    return client, refreshed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_supabase_client.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/supabase_client.py tests/test_supabase_client.py
git commit -m "feat: add sync/async Supabase client builders with session refresh policy"
```

---

### Task 10: `termcall/auth.py` + `signup`/`login`/`logout`/`whoami` commands

**Files:**
- Create: `termcall/auth.py`
- Modify: `main.py`
- Test: `tests/test_auth.py`, `tests/test_cli_auth.py`

**Interfaces:**
- Consumes: `termcall.session.{Session, save_session, clear_session}` (Task 8), `termcall.supabase_client.{build_sync_client, authenticated_sync_client, SessionExpiredError}` (Task 9).
- Produces: `signup(email: str, password: str) -> None`; `login(email: str, password: str) -> Session`; `logout() -> None`; `whoami() -> str`. `main.py` gains `signup`, `login`, `logout`, `whoami` commands.

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_auth.py
from unittest.mock import MagicMock, patch

from termcall.auth import login, logout, signup, whoami
from termcall.session import Session


def test_signup_calls_sign_up_with_credentials():
    fake_client = MagicMock()
    with patch("termcall.auth.build_sync_client", return_value=fake_client):
        signup("me@example.com", "hunter2")
    fake_client.auth.sign_up.assert_called_once_with({"email": "me@example.com", "password": "hunter2"})


def test_login_persists_session_and_returns_it():
    fake_client = MagicMock()
    fake_client.auth.sign_in_with_password.return_value.session.access_token = "a"
    fake_client.auth.sign_in_with_password.return_value.session.refresh_token = "r"
    fake_client.auth.sign_in_with_password.return_value.user.email = "me@example.com"
    saved = {}
    with patch("termcall.auth.build_sync_client", return_value=fake_client), \
         patch("termcall.auth.save_session", lambda s: saved.update(session=s)):
        session = login("me@example.com", "hunter2")
    assert session == Session(access_token="a", refresh_token="r", email="me@example.com")
    assert saved["session"] == session


def test_logout_clears_session():
    cleared = {"called": False}
    with patch("termcall.auth.clear_session", lambda: cleared.update(called=True)):
        logout()
    assert cleared["called"] is True


def test_whoami_returns_stored_email():
    with patch("termcall.auth.authenticated_sync_client", return_value=(MagicMock(), Session("a", "r", "me@example.com"))):
        assert whoami() == "me@example.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.auth'`

- [ ] **Step 3: Write `termcall/auth.py`**

```python
# termcall/auth.py
"""Signup/login/logout/whoami — thin wrappers around the sync Supabase client plus
keyring session storage. None of these need an event loop.
"""

from __future__ import annotations

from termcall.session import Session, clear_session, save_session
from termcall.supabase_client import authenticated_sync_client, build_sync_client


def signup(email: str, password: str) -> None:
    client = build_sync_client()
    client.auth.sign_up({"email": email, "password": password})


def login(email: str, password: str) -> Session:
    client = build_sync_client()
    response = client.auth.sign_in_with_password({"email": email, "password": password})
    session = Session(
        access_token=response.session.access_token,
        refresh_token=response.session.refresh_token,
        email=response.user.email,
    )
    save_session(session)
    return session


def logout() -> None:
    clear_session()


def whoami() -> str:
    _client, session = authenticated_sync_client()
    return session.email
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `uv run pytest tests/test_auth.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing CLI test**

```python
# tests/test_cli_auth.py
from unittest.mock import patch

from click.testing import CliRunner

from main import cli
from termcall.session import Session
from termcall.supabase_client import SessionExpiredError


def test_signup_prompts_and_calls_auth_signup():
    with patch("termcall.auth.signup") as mock_signup:
        result = CliRunner().invoke(cli, ["signup"], input="me@example.com\nhunter2\nhunter2\n")
    assert result.exit_code == 0
    mock_signup.assert_called_once_with("me@example.com", "hunter2")


def test_login_prompts_and_prints_confirmation():
    with patch("termcall.auth.login", return_value=Session("a", "r", "me@example.com")) as mock_login:
        result = CliRunner().invoke(cli, ["login"], input="me@example.com\nhunter2\n")
    assert result.exit_code == 0
    assert "me@example.com" in result.output
    mock_login.assert_called_once_with("me@example.com", "hunter2")


def test_whoami_reports_expired_session_as_click_exception():
    with patch("termcall.auth.whoami", side_effect=SessionExpiredError("no session")):
        result = CliRunner().invoke(cli, ["whoami"])
    assert result.exit_code != 0
    assert "termcall login" in result.output


def test_logout_calls_auth_logout():
    with patch("termcall.auth.logout") as mock_logout:
        result = CliRunner().invoke(cli, ["logout"])
    assert result.exit_code == 0
    mock_logout.assert_called_once()
```

- [ ] **Step 6: Run CLI test to verify it fails**

Run: `uv run pytest tests/test_cli_auth.py -v`
Expected: FAIL with `Error: No such command 'signup'.` (or similar) for each test

- [ ] **Step 7: Wire the commands into `main.py`**

```python
# main.py — add near the top, alongside the existing `preview_mod` import
from termcall import auth
from termcall.supabase_client import SessionExpiredError

# main.py — add after the preview command
@cli.command("signup")
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
def signup_cmd(email: str, password: str) -> None:
    """Create a new termcall account."""
    auth.signup(email, password)
    click.echo("Account created. Run `termcall login` to sign in.")


@cli.command("login")
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True)
def login_cmd(email: str, password: str) -> None:
    """Sign in and persist a session in the OS keyring."""
    session = auth.login(email, password)
    click.echo(f"Logged in as {session.email}.")


@cli.command("logout")
def logout_cmd() -> None:
    """Clear the locally stored session."""
    auth.logout()
    click.echo("Logged out.")


@cli.command("whoami")
def whoami_cmd() -> None:
    """Print the logged-in email."""
    try:
        email = auth.whoami()
    except SessionExpiredError:
        raise click.ClickException("Session expired. Run `termcall login` again.") from None
    click.echo(email)
```

- [ ] **Step 8: Run CLI test to verify it passes**

Run: `uv run pytest tests/test_cli_auth.py -v`
Expected: PASS (4 passed)

- [ ] **Step 9: Commit**

```bash
git add termcall/auth.py main.py tests/test_auth.py tests/test_cli_auth.py
git commit -m "feat: add signup/login/logout/whoami commands"
```

---

### Task 11: `termcall/rooms.py` — room lifecycle operations

**Files:**
- Create: `termcall/rooms.py`
- Test: `tests/test_rooms.py`

**Interfaces:**
- Produces: `Room(id, code, host_id, status)`, `Member(id, room_id, user_id, joined_at, left_at)` (frozen dataclasses); `RoomNotJoinableError(Exception)`, `RoomFullError(Exception)`; `generate_room_code(length=6) -> str`; `parse_room(row: dict) -> Room`; `parse_member(row: dict) -> Member`; `async def create_room(client, host_id) -> Room`; `async def join_room(client, code) -> Room`; `async def leave_room(client, room_id) -> None`; `async def fetch_active_roster(client, room_id) -> list[Member]`. `Member`/`parse_member` are consumed directly by `termcall/signaling.py` (Task 12/13).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rooms.py
from unittest.mock import AsyncMock, MagicMock

import pytest
from postgrest.exceptions import APIError

from termcall.rooms import (
    ROOM_CODE_ALPHABET,
    ROOM_CODE_LENGTH,
    Member,
    Room,
    RoomFullError,
    RoomNotJoinableError,
    create_room,
    fetch_active_roster,
    generate_room_code,
    join_room,
    leave_room,
)


def test_generate_room_code_uses_unambiguous_alphabet_and_requested_length():
    code = generate_room_code()
    assert len(code) == ROOM_CODE_LENGTH
    assert all(c in ROOM_CODE_ALPHABET for c in code)
    assert "0" not in code and "O" not in code and "1" not in code and "I" not in code


def _table_mock(execute_result):
    table = MagicMock()
    table.insert.return_value.execute = AsyncMock(return_value=execute_result)
    table.select.return_value.eq.return_value.is_.return_value.order.return_value.execute = AsyncMock(
        return_value=execute_result
    )
    return table


async def test_create_room_inserts_then_joins_as_first_member():
    client = MagicMock()
    insert_result = MagicMock(data=[{"id": "room-1", "code": "ABC123", "host_id": "host-1", "status": "open"}])
    client.table.return_value = _table_mock(insert_result)
    client.rpc.return_value.execute = AsyncMock()

    room = await create_room(client, "host-1")

    assert room == Room(id="room-1", code="ABC123", host_id="host-1", status="open")
    client.rpc.assert_called_once_with("join_room", {"p_code": "ABC123"})


async def test_join_room_maps_room_full_error():
    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock(side_effect=APIError({"message": "room full"}))
    with pytest.raises(RoomFullError):
        await join_room(client, "ABC123")


async def test_join_room_maps_room_not_joinable_error():
    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock(side_effect=APIError({"message": "room not joinable"}))
    with pytest.raises(RoomNotJoinableError):
        await join_room(client, "ABC123")


async def test_join_room_returns_room_on_success():
    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock(
        return_value=MagicMock(data={"id": "room-1", "code": "ABC123", "host_id": "host-1", "status": "open"})
    )
    room = await join_room(client, "ABC123")
    assert room == Room(id="room-1", code="ABC123", host_id="host-1", status="open")


async def test_leave_room_calls_rpc():
    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock()
    await leave_room(client, "room-1")
    client.rpc.assert_called_once_with("leave_room", {"p_room_id": "room-1"})


async def test_fetch_active_roster_parses_members_ordered_by_join():
    client = MagicMock()
    result = MagicMock(
        data=[
            {"id": 1, "room_id": "room-1", "user_id": "u1", "joined_at": "t1", "left_at": None},
            {"id": 2, "room_id": "room-1", "user_id": "u2", "joined_at": "t2", "left_at": None},
        ]
    )
    client.table.return_value = _table_mock(result)
    roster = await fetch_active_roster(client, "room-1")
    assert roster == [
        Member(id=1, room_id="room-1", user_id="u1", joined_at="t1", left_at=None),
        Member(id=2, room_id="room-1", user_id="u2", joined_at="t2", left_at=None),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rooms.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.rooms'`

- [ ] **Step 3: Write the implementation**

```python
# termcall/rooms.py
"""Room lifecycle: code generation, create/join/leave via the join_room/leave_room
RPCs (design §4.2), and active-roster lookups. Every function takes an authenticated
AsyncClient; error mapping matches design §10.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from postgrest.exceptions import APIError
from supabase import AsyncClient

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # excludes 0/O, 1/I
ROOM_CODE_LENGTH = 6
MAX_CODE_ATTEMPTS = 5


@dataclass(frozen=True)
class Room:
    id: str
    code: str
    host_id: str
    status: str


@dataclass(frozen=True)
class Member:
    id: int
    room_id: str
    user_id: str
    joined_at: str
    left_at: str | None


class RoomNotJoinableError(Exception):
    """Raised when a room code doesn't exist or the room has already ended."""


class RoomFullError(Exception):
    """Raised when a room already has 4 active participants."""


def generate_room_code(length: int = ROOM_CODE_LENGTH) -> str:
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(length))


def parse_room(row: dict) -> Room:
    return Room(id=row["id"], code=row["code"], host_id=row["host_id"], status=row["status"])


def parse_member(row: dict) -> Member:
    return Member(
        id=row["id"],
        room_id=row["room_id"],
        user_id=row["user_id"],
        joined_at=row["joined_at"],
        left_at=row.get("left_at"),
    )


async def create_room(client: AsyncClient, host_id: str) -> Room:
    for _ in range(MAX_CODE_ATTEMPTS):
        code = generate_room_code()
        try:
            resp = await client.table("rooms").insert({"code": code, "host_id": host_id, "status": "open"}).execute()
        except APIError as err:
            if "duplicate key" in str(err).lower():
                continue
            raise
        room = parse_room(resp.data[0])
        break
    else:
        raise RuntimeError("could not allocate a unique room code")

    await client.rpc("join_room", {"p_code": room.code}).execute()
    return room


async def join_room(client: AsyncClient, code: str) -> Room:
    try:
        resp = await client.rpc("join_room", {"p_code": code}).execute()
    except APIError as err:
        message = str(err).lower()
        if "room full" in message:
            raise RoomFullError(str(err)) from err
        if "room not joinable" in message:
            raise RoomNotJoinableError(str(err)) from err
        raise
    return parse_room(resp.data)


async def leave_room(client: AsyncClient, room_id: str) -> None:
    await client.rpc("leave_room", {"p_room_id": room_id}).execute()


async def fetch_active_roster(client: AsyncClient, room_id: str) -> list[Member]:
    resp = (
        await client.table("room_members")
        .select("*")
        .eq("room_id", room_id)
        .is_("left_at", "null")
        .order("id")
        .execute()
    )
    return [parse_member(row) for row in resp.data]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_rooms.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/rooms.py tests/test_rooms.py
git commit -m "feat: add room lifecycle operations (create/join/leave/roster)"
```

---

### Task 12: `termcall/signaling.py` — payloads and offerer determinism

**Files:**
- Create: `termcall/signaling.py`
- Test: `tests/test_signaling.py`

**Interfaces:**
- Produces: `Signal(id, room_id, sender_id, recipient_id, kind, payload)` (frozen dataclass); `am_i_offerer(my_member_id: int, peer_member_id: int) -> bool`; `build_offer_payload(sdp) -> dict`; `build_answer_payload(sdp) -> dict`; `build_ice_payload(candidate, sdp_mid, sdp_mline_index) -> dict`; `parse_signal(row: dict) -> Signal`; `async def insert_signal(client, *, room_id, sender_id, recipient_id, kind, payload) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signaling.py
from unittest.mock import AsyncMock, MagicMock

import pytest

from termcall.signaling import (
    Signal,
    am_i_offerer,
    build_answer_payload,
    build_ice_payload,
    build_offer_payload,
    insert_signal,
    parse_signal,
)


@pytest.mark.parametrize(
    "my_id,peer_id,expected",
    [(1, 2, True), (2, 1, False), (5, 100, True), (100, 5, False)],
)
def test_am_i_offerer_lower_member_id_joined_earlier(my_id, peer_id, expected):
    assert am_i_offerer(my_id, peer_id) is expected


def test_am_i_offerer_rejects_self_comparison():
    with pytest.raises(ValueError):
        am_i_offerer(1, 1)


def test_payload_shapes():
    assert build_offer_payload("v=0...") == {"sdp": "v=0..."}
    assert build_answer_payload("v=0...") == {"sdp": "v=0..."}
    assert build_ice_payload("candidate:1 1 udp...", "0", 0) == {
        "candidate": "candidate:1 1 udp...",
        "sdpMid": "0",
        "sdpMLineIndex": 0,
    }


def test_parse_signal_round_trips_row_shape():
    row = {
        "id": 42,
        "room_id": "room-1",
        "sender_id": "u1",
        "recipient_id": "u2",
        "kind": "offer",
        "payload": {"sdp": "v=0..."},
    }
    assert parse_signal(row) == Signal(
        id=42, room_id="room-1", sender_id="u1", recipient_id="u2", kind="offer", payload={"sdp": "v=0..."}
    )


async def test_insert_signal_writes_expected_row():
    client = MagicMock()
    client.table.return_value.insert.return_value.execute = AsyncMock()
    await insert_signal(client, room_id="room-1", sender_id="u1", recipient_id="u2", kind="offer", payload={"sdp": "x"})
    client.table.assert_called_once_with("signals")
    client.table.return_value.insert.assert_called_once_with(
        {
            "room_id": "room-1",
            "sender_id": "u1",
            "recipient_id": "u2",
            "kind": "offer",
            "payload": {"sdp": "x"},
        }
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_signaling.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.signaling'`

- [ ] **Step 3: Write the implementation**

```python
# termcall/signaling.py
"""WebRTC signaling: payload shaping/parsing and offerer/answerer determinism
(design §6). Realtime subscriptions are added in Task 13.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import AsyncClient


@dataclass(frozen=True)
class Signal:
    id: int
    room_id: str
    sender_id: str
    recipient_id: str
    kind: str  # "offer" | "answer" | "ice"
    payload: dict


def am_i_offerer(my_member_id: int, peer_member_id: int) -> bool:
    """`room_members.id` is an always-increasing identity column, so the row with the
    lower id joined first. Design §6: whoever joined earlier offers to whoever joined
    later. Ids are globally unique, so this never ties.
    """
    if my_member_id == peer_member_id:
        raise ValueError("a member cannot be compared against itself")
    return my_member_id < peer_member_id


def build_offer_payload(sdp: str) -> dict:
    return {"sdp": sdp}


def build_answer_payload(sdp: str) -> dict:
    return {"sdp": sdp}


def build_ice_payload(candidate: str, sdp_mid: str | None, sdp_mline_index: int | None) -> dict:
    return {"candidate": candidate, "sdpMid": sdp_mid, "sdpMLineIndex": sdp_mline_index}


def parse_signal(row: dict) -> Signal:
    return Signal(
        id=row["id"],
        room_id=row["room_id"],
        sender_id=row["sender_id"],
        recipient_id=row["recipient_id"],
        kind=row["kind"],
        payload=row["payload"],
    )


async def insert_signal(
    client: AsyncClient, *, room_id: str, sender_id: str, recipient_id: str, kind: str, payload: dict
) -> None:
    await client.table("signals").insert(
        {
            "room_id": room_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "kind": kind,
            "payload": payload,
        }
    ).execute()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_signaling.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/signaling.py tests/test_signaling.py
git commit -m "feat: add signaling payload shaping and offerer/answerer determinism"
```

---

### Task 13: Realtime subscriptions (`room_members`, `signals`)

**Files:**
- Modify: `termcall/signaling.py`
- Test: `tests/test_signaling_subscriptions.py`

**Interfaces:**
- Consumes: `termcall.rooms.{Member, parse_member}` (Task 11), `Signal`/`parse_signal` (Task 12).
- Produces: `async def subscribe_room_members(client, room_id, on_member: Callable[[Member], Awaitable[None]]) -> tuple[list[Member], channel]`; `async def subscribe_signals(client, room_id, my_user_id, on_signal: Callable[[Signal], Awaitable[None]]) -> tuple[list[Signal], channel]`. Both SELECT backlog before subscribing (design §6) — this ordering is the behavior under test. `channel` exposes `.unsubscribe()` for cleanup, consumed by `main.py` (Task 22).

**Implementation note for the executor:** the exact shape of `on_postgres_changes` callback payloads (e.g. whether the new row lives at `payload["data"]["record"]`) depends on the installed `realtime`/`supabase` package version. Verify it against the actually-installed version (`uv run python -c "import realtime; print(realtime.__version__)"` and check its source) the first time this code runs against a live local Supabase instance (Task 22's manual verification), and adjust `_on_insert`'s payload unpacking if it differs — the unit tests below pin our own assumed shape and will keep passing regardless since they control the fake channel's callback invocation.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signaling_subscriptions.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

from termcall.rooms import Member
from termcall.signaling import Signal, subscribe_room_members, subscribe_signals


def _room_members_client(table_data):
    client = MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value
    chain.is_.return_value.execute = AsyncMock(return_value=MagicMock(data=table_data))
    return client


def _signals_client(table_data):
    client = MagicMock()
    chain = client.table.return_value.select.return_value.eq.return_value.eq.return_value
    chain.execute = AsyncMock(return_value=MagicMock(data=table_data))
    return client


async def test_subscribe_room_members_selects_backlog_before_subscribing():
    client = _room_members_client(
        [{"id": 1, "room_id": "room-1", "user_id": "u1", "joined_at": "t1", "left_at": None}]
    )
    fake_channel = MagicMock()
    fake_channel.on_postgres_changes.side_effect = lambda *a, **k: calls.append("registered_callback") or fake_channel
    fake_channel.subscribe.side_effect = lambda *a, **k: calls.append("subscribed")
    client.channel.return_value = fake_channel

    async def on_member(member):
        calls.append(("member", member))

    backlog, channel = await subscribe_room_members(client, "room-1", on_member)

    assert backlog == [Member(id=1, room_id="room-1", user_id="u1", joined_at="t1", left_at=None)]
    assert calls == ["registered_callback", "subscribed"]  # backlog resolved before this call even runs
    assert channel is fake_channel


async def test_subscribe_room_members_dispatches_inserts_to_callback():
    client = _room_members_client([])
    fake_channel = MagicMock()
    captured_callback = {}

    def on_postgres_changes(*args, **kwargs):
        captured_callback["fn"] = kwargs["callback"]
        return fake_channel

    fake_channel.on_postgres_changes.side_effect = on_postgres_changes
    client.channel.return_value = fake_channel

    received = []

    async def on_member(member):
        received.append(member)

    await subscribe_room_members(client, "room-1", on_member)
    captured_callback["fn"]({"data": {"record": {"id": 2, "room_id": "room-1", "user_id": "u2", "joined_at": "t2", "left_at": None}}})

    await asyncio.sleep(0)
    assert received == [Member(id=2, room_id="room-1", user_id="u2", joined_at="t2", left_at=None)]


async def test_subscribe_signals_filters_backlog_by_recipient():
    client = _signals_client(
        [{"id": 1, "room_id": "room-1", "sender_id": "u1", "recipient_id": "u2", "kind": "offer", "payload": {"sdp": "x"}}]
    )
    client.channel.return_value = MagicMock()

    async def on_signal(signal):
        pass

    backlog, _channel = await subscribe_signals(client, "room-1", "u2", on_signal)
    assert backlog == [Signal(id=1, room_id="room-1", sender_id="u1", recipient_id="u2", kind="offer", payload={"sdp": "x"})]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_signaling_subscriptions.py -v`
Expected: FAIL with `ImportError: cannot import name 'subscribe_room_members'`

- [ ] **Step 3: Add the subscription functions**

```python
# termcall/signaling.py — add imports and append these functions
import asyncio
from collections.abc import Awaitable, Callable

from termcall.rooms import Member, parse_member


async def subscribe_room_members(
    client: AsyncClient, room_id: str, on_member: Callable[[Member], Awaitable[None]]
):
    """SELECT the current active roster, then subscribe to future INSERTs — a member
    joining mid-call must see who's already there (design §6).
    """
    backlog_resp = (
        await client.table("room_members").select("*").eq("room_id", room_id).is_("left_at", "null").execute()
    )
    backlog = [parse_member(row) for row in backlog_resp.data]

    def _on_insert(payload: dict) -> None:
        member = parse_member(payload["data"]["record"])
        asyncio.ensure_future(on_member(member))

    channel = client.channel(f"room_members:{room_id}")
    channel.on_postgres_changes(
        "INSERT", schema="public", table="room_members", filter=f"room_id=eq.{room_id}", callback=_on_insert
    )
    channel.subscribe()
    return backlog, channel


async def subscribe_signals(
    client: AsyncClient, room_id: str, my_user_id: str, on_signal: Callable[[Signal], Awaitable[None]]
):
    """SELECT signals already addressed to me, then subscribe to future INSERTs."""
    backlog_resp = (
        await client.table("signals")
        .select("*")
        .eq("room_id", room_id)
        .eq("recipient_id", my_user_id)
        .execute()
    )
    backlog = [parse_signal(row) for row in backlog_resp.data]

    def _on_insert(payload: dict) -> None:
        signal = parse_signal(payload["data"]["record"])
        asyncio.ensure_future(on_signal(signal))

    channel = client.channel(f"signals:{my_user_id}")
    channel.on_postgres_changes(
        "INSERT", schema="public", table="signals", filter=f"recipient_id=eq.{my_user_id}", callback=_on_insert
    )
    channel.subscribe()
    return backlog, channel
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_signaling_subscriptions.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/signaling.py tests/test_signaling_subscriptions.py
git commit -m "feat: add backlog-then-stream Realtime subscriptions for room_members/signals"
```

---

### Task 14: `termcall/grid.py` — grid layout math and compositor

**Files:**
- Create: `termcall/grid.py`
- Test: `tests/test_grid.py`

**Interfaces:**
- Produces: `grid_dimensions(n: int) -> tuple[int, int]`; `compose_grid(tiles: list[str], cols: int, rows: int, blank_tile: str) -> str`. Consumed by `termcall/call.py` (Task 21), which supplies pre-rendered ANSI tile strings (via `render.frame_to_ansi`) and a `blank_tile` of matching dimensions.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grid.py
import pytest

from termcall.grid import compose_grid, grid_dimensions


@pytest.mark.parametrize(
    "n,expected",
    [(1, (1, 1)), (2, (2, 1)), (3, (2, 2)), (4, (2, 2))],
)
def test_grid_dimensions_matches_design_table(n, expected):
    assert grid_dimensions(n) == expected


def test_grid_dimensions_rejects_zero_or_negative():
    with pytest.raises(ValueError):
        grid_dimensions(0)


def test_compose_grid_interleaves_two_full_rows():
    tiles = ["A\nB", "C\nD"]
    assert compose_grid(tiles, cols=2, rows=1, blank_tile="X\nX") == "A  C\nB  D"


def test_compose_grid_pads_missing_cells_with_blank_tile():
    tiles = ["A\nB", "C\nD", "E\nF"]
    result = compose_grid(tiles, cols=2, rows=2, blank_tile="X\nX")
    assert result == "A  C\nB  D\nE  X\nF  X"


def test_compose_grid_rejects_empty_tiles():
    with pytest.raises(ValueError):
        compose_grid([], cols=1, rows=1, blank_tile="X")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_grid.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.grid'`

- [ ] **Step 3: Write the implementation**

```python
# termcall/grid.py
"""Grid layout math and compositor for the in-call n-way tile view (design §8)."""

from __future__ import annotations

import math


def grid_dimensions(n: int) -> tuple[int, int]:
    """cols = ceil(sqrt(n)), rows = ceil(n / cols) — design §8's fixed table for n=1..4."""
    if n < 1:
        raise ValueError("n must be >= 1")
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return cols, rows


def compose_grid(tiles: list[str], cols: int, rows: int, blank_tile: str) -> str:
    """Interleave pre-rendered ANSI tile strings (each the same number of lines) into
    one multi-row grid, left-to-right then top-to-bottom, padding with `blank_tile`
    when there are fewer tiles than `cols * rows`.
    """
    if not tiles:
        raise ValueError("tiles must be non-empty")
    tile_lines = [t.split("\n") for t in tiles]
    tile_height = len(tile_lines[0])
    blank_lines = blank_tile.split("\n")
    total_cells = cols * rows
    padded = tile_lines + [blank_lines] * (total_cells - len(tile_lines))

    grid_lines = []
    for r in range(rows):
        row_tiles = padded[r * cols : (r + 1) * cols]
        for line_idx in range(tile_height):
            grid_lines.append("  ".join(t[line_idx] for t in row_tiles))
    return "\n".join(grid_lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_grid.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/grid.py tests/test_grid.py
git commit -m "feat: add grid layout math and tile compositor"
```

---

### Task 15: `termcall/media.py` — `LocalVideoTrack`

**Files:**
- Create: `termcall/media.py`
- Test: `tests/test_media_video.py`

**Interfaces:**
- Produces: `LocalVideoTrack(VideoStreamTrack)` — `__init__(self, frame_source: Callable[[], np.ndarray], fps: float)`, `async def recv(self) -> av.VideoFrame`, `self.enabled: bool` (default `True`; when `False`, `recv()` returns a black frame instead of a real one — spec §8's "stops sending frames without dropping any connection"). `frame_source` returns a BGR `(H, W, 3)` uint8 array (matching `cv2.VideoCapture.read()`'s second return value).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_media_video.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.media'`

- [ ] **Step 3: Write the implementation**

```python
# termcall/media.py
"""aiortc media tracks wrapping the local camera and microphone (design §7)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from fractions import Fraction

import av
import numpy as np
from aiortc.mediastreams import VideoStreamTrack

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_media_video.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/media.py tests/test_media_video.py
git commit -m "feat: add LocalVideoTrack wrapping the camera capture loop"
```

---

### Task 16: `termcall/media.py` — `LocalAudioTrack`

**Files:**
- Modify: `termcall/media.py`
- Test: `tests/test_media_audio.py`

**Interfaces:**
- Consumes: nothing new beyond stdlib/`av`.
- Produces: `AUDIO_SAMPLE_RATE = 48000`, `AUDIO_SAMPLES_PER_FRAME = 960` (20ms); `LocalAudioTrack(AudioStreamTrack)` — `__init__(self, queue: asyncio.Queue)`, `async def recv(self) -> av.AudioFrame`, `self.muted: bool` (default `False`; when `True`, `recv()` still drains the queue but zeroes the samples, so pacing/track liveness is unaffected).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_media_audio.py -v`
Expected: FAIL with `ImportError: cannot import name 'LocalAudioTrack'`

- [ ] **Step 3: Append the implementation**

```python
# termcall/media.py — add these imports at the top alongside the existing ones
from aiortc.mediastreams import AudioStreamTrack

# termcall/media.py — append after LocalVideoTrack
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
        chunk = await self._queue.get()
        if self.muted:
            chunk = np.zeros_like(chunk)
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = AUDIO_SAMPLE_RATE
        frame.pts = self._samples_sent
        frame.time_base = AUDIO_TIME_BASE
        self._samples_sent += chunk.shape[0]
        return frame
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_media_audio.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/media.py tests/test_media_audio.py
git commit -m "feat: add LocalAudioTrack wrapping queued microphone PCM chunks"
```

---

### Task 17: Camera/mic device opening + `MediaRelay` fan-out wiring

**Files:**
- Modify: `termcall/media.py`
- Test: `tests/test_media_devices.py`

**Interfaces:**
- Produces: `open_camera(device: int) -> cv2.VideoCapture` (raises `DeviceError` if unopenable); `open_microphone(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> sounddevice.InputStream` (raises `DeviceError` on `sounddevice.PortAudioError`; returned stream is a context manager the caller `with`s). Both consumed by `main.py` (Task 22).

- [ ] **Step 1: Write the failing test**

```python
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
    with patch("termcall.media.sd.InputStream", side_effect=sounddevice.PortAudioError("no default input device")):
        with pytest.raises(DeviceError, match="microphone"):
            open_microphone(asyncio.Queue(), asyncio.get_event_loop())


def test_open_microphone_callback_pushes_chunks_onto_queue_thread_safely():
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    captured = {}

    def fake_input_stream(*, samplerate, blocksize, channels, dtype, callback, **kwargs):
        captured["callback"] = callback
        return MagicMock()

    with patch("termcall.media.sd.InputStream", side_effect=fake_input_stream):
        open_microphone(queue, loop)

    chunk = np.zeros((AUDIO_SAMPLES_PER_FRAME, 1), dtype=np.int16)
    captured["callback"](chunk, AUDIO_SAMPLES_PER_FRAME, None, None)
    assert queue.qsize() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_media_devices.py -v`
Expected: FAIL with `ImportError: cannot import name 'open_camera'`

- [ ] **Step 3: Append the implementation**

```python
# termcall/media.py — add these imports at the top alongside the existing ones
import cv2
import sounddevice as sd

# termcall/media.py — append after LocalAudioTrack
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_media_devices.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/media.py tests/test_media_devices.py
git commit -m "feat: add camera/microphone device opening"
```

---

### Task 18: `termcall/peers.py` — `PeerConnectionManager`

**Files:**
- Create: `termcall/peers.py`
- Test: `tests/test_peers.py` (unit, mocked `aiortc`), `tests/test_peers_integration.py` (real in-process two-peer connection)

**Interfaces:**
- Produces: `PeerConnectionManager` — `__init__(self, *, on_video_frame, on_audio_frame, on_state_change, ice_servers=None)`; `async def create_offer(self, peer_id, local_tracks) -> RTCSessionDescription`; `async def accept_offer(self, peer_id, offer_sdp, local_tracks) -> RTCSessionDescription`; `async def accept_answer(self, peer_id, answer_sdp) -> None`; `async def add_ice_candidate(self, peer_id, payload: dict) -> None`; `async def close(self, peer_id) -> None`; `async def close_all(self) -> None`. Callback signatures: `on_video_frame(peer_id: str, frame: np.ndarray)` (RGB), `on_audio_frame(peer_id: str, frame: np.ndarray)` (int16 mono), `on_state_change(peer_id: str, state: str)`.

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_peers.py
from unittest.mock import AsyncMock, MagicMock, patch

from termcall.peers import PeerConnectionManager


def _manager():
    return PeerConnectionManager(
        on_video_frame=MagicMock(),
        on_audio_frame=MagicMock(),
        on_state_change=MagicMock(),
        ice_servers=[],
    )


async def test_create_offer_adds_relayed_tracks_and_waits_for_ice_complete():
    manager = _manager()
    fake_pc = MagicMock()
    fake_pc.createOffer = AsyncMock(return_value=MagicMock(sdp="offer-sdp"))
    fake_pc.setLocalDescription = AsyncMock()
    fake_pc.localDescription = MagicMock(sdp="offer-sdp")
    type(fake_pc).iceGatheringState = "complete"

    fake_relay = MagicMock()
    fake_relay.subscribe.return_value = "relayed-track"

    with patch("termcall.peers.RTCPeerConnection", return_value=fake_pc):
        manager._relay = fake_relay
        result = await manager.create_offer("peer-1", ["video-track", "audio-track"])

    assert fake_pc.addTrack.call_count == 2
    fake_relay.subscribe.assert_any_call("video-track")
    fake_relay.subscribe.assert_any_call("audio-track")
    assert result.sdp == "offer-sdp"


async def test_close_removes_and_closes_the_connection():
    manager = _manager()
    fake_pc = AsyncMock()
    manager._connections["peer-1"] = fake_pc
    await manager.close("peer-1")
    fake_pc.close.assert_awaited_once()
    assert "peer-1" not in manager._connections


async def test_close_all_closes_every_connection():
    manager = _manager()
    pc_a, pc_b = AsyncMock(), AsyncMock()
    manager._connections = {"a": pc_a, "b": pc_b}
    await manager.close_all()
    pc_a.close.assert_awaited_once()
    pc_b.close.assert_awaited_once()
    assert manager._connections == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_peers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.peers'`

- [ ] **Step 3: Write the implementation**

```python
# termcall/peers.py
"""One RTCPeerConnection per peer, keyed by user_id, with MediaRelay fan-out so a
single local video/audio track can feed up to 3 simultaneous connections (design §7).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import numpy as np
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.sdp import candidate_from_sdp

ICE_SERVERS = [RTCIceServer(urls="stun:stun.l.google.com:19302")]

OnVideoFrame = Callable[[str, np.ndarray], None]
OnAudioFrame = Callable[[str, np.ndarray], None]
OnStateChange = Callable[[str, str], None]


class PeerConnectionManager:
    def __init__(
        self,
        *,
        on_video_frame: OnVideoFrame,
        on_audio_frame: OnAudioFrame,
        on_state_change: OnStateChange,
        ice_servers: list[RTCIceServer] | None = None,
    ) -> None:
        self._connections: dict[str, RTCPeerConnection] = {}
        self._relay = MediaRelay()
        self._on_video_frame = on_video_frame
        self._on_audio_frame = on_audio_frame
        self._on_state_change = on_state_change
        self._ice_servers = ICE_SERVERS if ice_servers is None else ice_servers

    def _new_connection(self, peer_id: str) -> RTCPeerConnection:
        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=self._ice_servers))
        self._connections[peer_id] = pc

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            self._on_state_change(peer_id, pc.connectionState)

        @pc.on("track")
        def on_track(track) -> None:
            asyncio.ensure_future(self._consume_track(peer_id, track))

        return pc

    async def _consume_track(self, peer_id: str, track) -> None:
        while True:
            try:
                frame = await track.recv()
            except Exception:
                return
            if track.kind == "video":
                self._on_video_frame(peer_id, frame.to_ndarray(format="rgb24"))
            else:
                self._on_audio_frame(peer_id, frame.to_ndarray().reshape(-1))

    async def _await_ice_gathering(self, pc: RTCPeerConnection) -> None:
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.05)

    async def create_offer(self, peer_id: str, local_tracks: list) -> RTCSessionDescription:
        pc = self._new_connection(peer_id)
        for track in local_tracks:
            pc.addTrack(self._relay.subscribe(track))
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await self._await_ice_gathering(pc)
        return pc.localDescription

    async def accept_offer(self, peer_id: str, offer_sdp: str, local_tracks: list) -> RTCSessionDescription:
        pc = self._new_connection(peer_id)
        for track in local_tracks:
            pc.addTrack(self._relay.subscribe(track))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._await_ice_gathering(pc)
        return pc.localDescription

    async def accept_answer(self, peer_id: str, answer_sdp: str) -> None:
        pc = self._connections[peer_id]
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))

    async def add_ice_candidate(self, peer_id: str, payload: dict) -> None:
        pc = self._connections.get(peer_id)
        if pc is None:
            return
        candidate = candidate_from_sdp(payload["candidate"].split(":", 1)[1])
        candidate.sdpMid = payload.get("sdpMid")
        candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
        await pc.addIceCandidate(candidate)

    async def close(self, peer_id: str) -> None:
        pc = self._connections.pop(peer_id, None)
        if pc is not None:
            await pc.close()

    async def close_all(self) -> None:
        await asyncio.gather(*(pc.close() for pc in self._connections.values()))
        self._connections.clear()
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `uv run pytest tests/test_peers.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Write the real in-process integration test**

```python
# tests/test_peers_integration.py
"""Connects two real PeerConnectionManager instances to each other in-process over
loopback host candidates (no STUN needed on one machine) — proves offer/answer/ICE
actually establishes a working RTCPeerConnection and routes media frames.
"""

import asyncio

import numpy as np
import pytest

from termcall.media import LocalVideoTrack
from termcall.peers import PeerConnectionManager


@pytest.mark.timeout(30)
async def test_two_peers_connect_and_exchange_video():
    a_frames: list[np.ndarray] = []
    b_frames: list[np.ndarray] = []
    a_states: list[str] = []
    b_states: list[str] = []

    manager_a = PeerConnectionManager(
        on_video_frame=lambda peer_id, frame: a_frames.append(frame),
        on_audio_frame=lambda peer_id, frame: None,
        on_state_change=lambda peer_id, state: a_states.append(state),
        ice_servers=[],
    )
    manager_b = PeerConnectionManager(
        on_video_frame=lambda peer_id, frame: b_frames.append(frame),
        on_audio_frame=lambda peer_id, frame: None,
        on_state_change=lambda peer_id, state: b_states.append(state),
        ice_servers=[],
    )

    track_a = LocalVideoTrack(frame_source=lambda: np.full((4, 4, 3), 42, dtype=np.uint8), fps=30.0)
    track_b = LocalVideoTrack(frame_source=lambda: np.full((4, 4, 3), 84, dtype=np.uint8), fps=30.0)

    offer = await manager_a.create_offer("b", [track_a])
    answer = await manager_b.accept_offer("a", offer.sdp, [track_b])
    await manager_a.accept_answer("b", answer.sdp)

    for _ in range(200):
        if "connected" in a_states and "connected" in b_states:
            break
        await asyncio.sleep(0.05)

    assert "connected" in a_states
    assert "connected" in b_states

    for _ in range(200):
        if a_frames and b_frames:
            break
        await asyncio.sleep(0.05)

    assert a_frames and (a_frames[0] == 84).all()  # A received B's frames
    assert b_frames and (b_frames[0] == 42).all()  # B received A's frames

    await manager_a.close_all()
    await manager_b.close_all()
```

`pytest-timeout` isn't in Task 3's dependency list — either add it (`uv add --group dev pytest-timeout`) or drop the `@pytest.mark.timeout(30)` decorator and rely on the bounded polling loops above, which already cap total wait at 20s. Prefer adding `pytest-timeout` since real ICE negotiation occasionally stalls and a hard kill is safer than a hung test run.

- [ ] **Step 6: Run integration test to verify it passes**

Run: `uv run pytest tests/test_peers_integration.py -v`
Expected: PASS (1 passed) — takes a few seconds for real ICE negotiation.

- [ ] **Step 7: Commit**

```bash
git add termcall/peers.py tests/test_peers.py tests/test_peers_integration.py pyproject.toml uv.lock
git commit -m "feat: add PeerConnectionManager with real two-peer integration test"
```

---

### Task 19: `termcall/call.py` — `CallSession` skeleton + membership-driven mesh formation

**Files:**
- Create: `termcall/call.py`
- Test: `tests/test_call_membership.py`

**Interfaces:**
- Consumes: `termcall.rooms.Member` (Task 11), `termcall.signaling.{Signal, am_i_offerer, build_offer_payload, build_answer_payload}` (Task 12), `termcall.peers.PeerConnectionManager` (Task 18).
- Produces: `CallSession.__init__(self, *, my_user_id, my_member_id, local_video_track, local_audio_track, camera_frame_source, send_signal, leave_room, target_width=None, frame_interval=1/20)`; `async def handle_member_joined(self, member: Member) -> None`; `async def handle_member_left(self, member: Member) -> None`; `async def handle_signal(self, signal: Signal) -> None`. Internal state (`self.roster: dict[str, Member]`, `self.video_tiles: dict[str, np.ndarray | None]`, `self.peers: PeerConnectionManager`, `self.failed_peers: set[str]`, `self.fatal_error: str | None`) is what Tasks 20–21 build on — do not rename these attributes. `self.fatal_error` implements design §10's "only peer connection failed" case: `main.py` (Task 22) checks it after `session.run()` returns and raises a non-zero-exit `click.ClickException` when set.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_call_membership.py
from unittest.mock import AsyncMock, MagicMock

from termcall.call import CallSession
from termcall.rooms import Member


def _session(my_member_id=10):
    return CallSession(
        my_user_id="me",
        my_member_id=my_member_id,
        local_video_track=MagicMock(),
        local_audio_track=MagicMock(),
        camera_frame_source=lambda: None,
        send_signal=AsyncMock(),
        leave_room=AsyncMock(),
    )


async def test_ignores_own_membership_row():
    session = _session()
    await session.handle_member_joined(Member(id=10, room_id="r", user_id="me", joined_at="t", left_at=None))
    assert session.roster == {}


async def test_offers_to_a_later_joining_member():
    session = _session(my_member_id=1)
    session.peers = MagicMock()
    session.peers.create_offer = AsyncMock(return_value=MagicMock(sdp="offer-sdp"))

    peer = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    await session.handle_member_joined(peer)

    assert session.roster == {"peer-1": peer}
    session.peers.create_offer.assert_awaited_once_with("peer-1", [session.local_video_track, session.local_audio_track])
    session.send_signal.assert_awaited_once_with("peer-1", "offer", {"sdp": "offer-sdp"})


async def test_only_answers_an_earlier_joined_member_no_offer_sent():
    session = _session(my_member_id=5)
    session.peers = MagicMock()
    session.peers.create_offer = AsyncMock()

    earlier = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    await session.handle_member_joined(earlier)

    assert session.roster == {"peer-1": earlier}
    session.peers.create_offer.assert_not_awaited()
    session.send_signal.assert_not_awaited()


async def test_member_left_drops_roster_tile_and_closes_peer():
    session = _session(my_member_id=1)
    session.peers = MagicMock()
    session.peers.close = AsyncMock()
    peer = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.roster["peer-1"] = peer
    session.video_tiles["peer-1"] = "some-frame"

    await session.handle_member_left(peer)

    assert "peer-1" not in session.roster
    assert "peer-1" not in session.video_tiles
    session.peers.close.assert_awaited_once_with("peer-1")


async def test_handle_offer_signal_answers_and_sends_back():
    session = _session(my_member_id=5)
    session.peers = MagicMock()
    session.peers.accept_offer = AsyncMock(return_value=MagicMock(sdp="answer-sdp"))

    from termcall.signaling import Signal

    signal = Signal(id=1, room_id="r", sender_id="peer-1", recipient_id="me", kind="offer", payload={"sdp": "offer-sdp"})
    await session.handle_signal(signal)

    session.peers.accept_offer.assert_awaited_once_with(
        "peer-1", "offer-sdp", [session.local_video_track, session.local_audio_track]
    )
    session.send_signal.assert_awaited_once_with("peer-1", "answer", {"sdp": "answer-sdp"})


async def test_handle_answer_signal_completes_negotiation():
    session = _session(my_member_id=1)
    session.peers = MagicMock()
    session.peers.accept_answer = AsyncMock()

    from termcall.signaling import Signal

    signal = Signal(id=2, room_id="r", sender_id="peer-1", recipient_id="me", kind="answer", payload={"sdp": "answer-sdp"})
    await session.handle_signal(signal)

    session.peers.accept_answer.assert_awaited_once_with("peer-1", "answer-sdp")


async def test_handle_ice_signal_forwards_candidate():
    session = _session(my_member_id=1)
    session.peers = MagicMock()
    session.peers.add_ice_candidate = AsyncMock()

    from termcall.signaling import Signal

    signal = Signal(
        id=3, room_id="r", sender_id="peer-1", recipient_id="me", kind="ice",
        payload={"candidate": "candidate:1 1 udp...", "sdpMid": "0", "sdpMLineIndex": 0},
    )
    await session.handle_signal(signal)

    session.peers.add_ice_candidate.assert_awaited_once_with("peer-1", signal.payload)


async def test_state_change_to_failed_marks_peer_when_others_remain():
    session = _session(my_member_id=1)
    peer_a = Member(id=2, room_id="r", user_id="peer-a", joined_at="t", left_at=None)
    peer_b = Member(id=3, room_id="r", user_id="peer-b", joined_at="t", left_at=None)
    session.roster = {"peer-a": peer_a, "peer-b": peer_b}

    session._on_state_change("peer-a", "failed")

    assert "peer-a" in session.failed_peers
    assert session.fatal_error is None
    assert not session._shutdown.is_set()


async def test_state_change_to_failed_sets_fatal_error_when_it_was_the_only_peer():
    session = _session(my_member_id=1)
    peer = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.roster = {"peer-1": peer}

    session._on_state_change("peer-1", "failed")

    assert "peer-1" in session.failed_peers
    assert session.fatal_error == (
        "Could not establish a direct connection with your peer "
        "(NAT traversal failed, no relay server configured)"
    )
    assert session._shutdown.is_set()


async def test_state_change_to_connected_clears_failed_marker():
    session = _session(my_member_id=1)
    session.failed_peers.add("peer-1")
    session._on_state_change("peer-1", "connected")
    assert "peer-1" not in session.failed_peers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_call_membership.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'termcall.call'`

- [ ] **Step 3: Write the implementation**

```python
# termcall/call.py
"""CallSession: ties room membership, signaling, peer connections, and rendering
together for `termcall room create`/`termcall room join` (design §6, §8, §9).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import numpy as np

from termcall.peers import PeerConnectionManager
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
        self._shutdown = asyncio.Event()

        self.peers = PeerConnectionManager(
            on_video_frame=self._on_video_frame,
            on_audio_frame=self._on_audio_frame,
            on_state_change=self._on_state_change,
        )

    def _local_tracks(self) -> list:
        return [self.local_video_track, self.local_audio_track]

    def _on_video_frame(self, peer_id: str, frame: np.ndarray) -> None:
        self.video_tiles[peer_id] = frame

    def _on_audio_frame(self, peer_id: str, frame: np.ndarray) -> None:
        pass  # wired to sounddevice output mixing in Task 22

    def _on_state_change(self, peer_id: str, state: str) -> None:
        """Design §10: a failed peer connection shows a persistent placeholder tile
        if other connections remain up; if it was our only peer, the call ends with a
        fatal, non-zero-exit error instead of silently hanging.
        """
        if state == "failed":
            self.failed_peers.add(peer_id)
            if len(self.roster) == 1:
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
        await self.peers.close(member.user_id)

    async def handle_signal(self, signal: Signal) -> None:
        if signal.kind == "offer":
            answer = await self.peers.accept_offer(signal.sender_id, signal.payload["sdp"], self._local_tracks())
            await self.send_signal(signal.sender_id, "answer", build_answer_payload(answer.sdp))
        elif signal.kind == "answer":
            await self.peers.accept_answer(signal.sender_id, signal.payload["sdp"])
        elif signal.kind == "ice":
            await self.peers.add_ice_candidate(signal.sender_id, signal.payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_call_membership.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/call.py tests/test_call_membership.py
git commit -m "feat: add CallSession skeleton with membership-driven mesh formation"
```

---

### Task 20: `CallSession` keyboard input handling

**Files:**
- Modify: `termcall/call.py`
- Test: `tests/test_call_keyboard.py`

**Interfaces:**
- Consumes: `termcall.render.QUIT_KEYS` (Task 1).
- Produces: `def handle_key(self, key: str) -> None`; `async def _leave(self) -> None`; `def install_keyboard_reader(self, loop: asyncio.AbstractEventLoop) -> None`; `def remove_keyboard_reader(self, loop: asyncio.AbstractEventLoop) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_call_keyboard.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

from termcall.call import CallSession


def _session():
    return CallSession(
        my_user_id="me",
        my_member_id=1,
        local_video_track=MagicMock(enabled=True),
        local_audio_track=MagicMock(muted=False),
        camera_frame_source=lambda: None,
        send_signal=AsyncMock(),
        leave_room=AsyncMock(),
    )


def test_m_key_toggles_audio_mute():
    session = _session()
    session.handle_key("m")
    assert session.local_audio_track.muted is True
    session.handle_key("m")
    assert session.local_audio_track.muted is False


def test_v_key_toggles_video_disabled_and_track_enabled():
    session = _session()
    session.handle_key("v")
    assert session.video_disabled is True
    assert session.local_video_track.enabled is False
    session.handle_key("v")
    assert session.video_disabled is False
    assert session.local_video_track.enabled is True


def test_other_keys_are_ignored():
    session = _session()
    session.handle_key("x")
    assert session.local_audio_track.muted is False
    assert session.video_disabled is False


async def test_leave_sets_shutdown_closes_peers_and_calls_leave_room():
    session = _session()
    session.peers = MagicMock()
    session.peers.close_all = AsyncMock()

    await session._leave()

    assert session._shutdown.is_set()
    session.peers.close_all.assert_awaited_once()
    session.leave_room.assert_awaited_once()


async def test_quit_key_schedules_leave():
    session = _session()
    session.peers = MagicMock()
    session.peers.close_all = AsyncMock()
    session.handle_key("q")
    await asyncio.sleep(0)
    session.leave_room.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_call_keyboard.py -v`
Expected: FAIL with `AttributeError: 'CallSession' object has no attribute 'handle_key'`

- [ ] **Step 3: Append the implementation**

```python
# termcall/call.py — add these imports at the top
import sys

from termcall.render import QUIT_KEYS

# termcall/call.py — add these methods to CallSession
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

    def install_keyboard_reader(self, loop: asyncio.AbstractEventLoop) -> None:
        loop.add_reader(sys.stdin.fileno(), self._on_stdin_readable)

    def remove_keyboard_reader(self, loop: asyncio.AbstractEventLoop) -> None:
        loop.remove_reader(sys.stdin.fileno())

    def _on_stdin_readable(self) -> None:
        char = sys.stdin.read(1)
        self.handle_key(char)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_call_keyboard.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/call.py tests/test_call_keyboard.py
git commit -m "feat: add in-call keyboard handling (leave/mute/video toggle)"
```

---

### Task 21: `CallSession` grid render loop

**Files:**
- Modify: `termcall/call.py`
- Test: `tests/test_call_render.py`

**Interfaces:**
- Consumes: `termcall.grid.{grid_dimensions, compose_grid}` (Task 14), `termcall.render.{frame_to_ansi, output_size, live_screen, CURSOR_HOME}` (Task 1).
- Produces: `def compose_frame(self, self_frame: np.ndarray | None) -> str`; `async def render_loop(self) -> None`; `async def run(self) -> None` (installs the keyboard reader, runs the render loop until `self._shutdown` is set, then removes the reader — the top-level coroutine `main.py` awaits).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_call_render.py
from unittest.mock import MagicMock

import numpy as np

from termcall.call import CallSession
from termcall.rooms import Member


def _session():
    return CallSession(
        my_user_id="me",
        my_member_id=1,
        local_video_track=MagicMock(),
        local_audio_track=MagicMock(),
        camera_frame_source=lambda: np.zeros((4, 4, 3), dtype=np.uint8),
        send_signal=None,
        leave_room=None,
        target_width=8,
    )


def test_compose_frame_with_only_self_is_a_single_tile():
    session = _session()
    frame = session.compose_frame(np.zeros((4, 4, 3), dtype=np.uint8))
    assert isinstance(frame, str)
    assert frame != ""


def test_compose_frame_orders_peers_by_join_order():
    session = _session()
    early = Member(id=2, room_id="r", user_id="early", joined_at="t", left_at=None)
    late = Member(id=3, room_id="r", user_id="late", joined_at="t", left_at=None)
    session.roster = {"late": late, "early": early}
    session.video_tiles = {"early": np.full((4, 4, 3), 10, dtype=np.uint8), "late": None}

    order = session._ordered_tile_user_ids()
    assert order == ["__self__", "early", "late"]


def test_compose_frame_uses_black_tile_for_missing_peer_frame():
    session = _session()
    peer = Member(id=2, room_id="r", user_id="peer-1", joined_at="t", left_at=None)
    session.roster = {"peer-1": peer}
    session.video_tiles = {"peer-1": None}
    frame = session.compose_frame(np.zeros((4, 4, 3), dtype=np.uint8))
    assert isinstance(frame, str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_call_render.py -v`
Expected: FAIL with `AttributeError: 'CallSession' object has no attribute 'compose_frame'`

- [ ] **Step 3: Append the implementation**

```python
# termcall/call.py — add these imports at the top
import time

import cv2

from termcall.grid import compose_grid, grid_dimensions
from termcall.render import CURSOR_HOME, frame_to_ansi, live_screen, output_size

# termcall/call.py — add these methods to CallSession
    def _ordered_tile_user_ids(self) -> list[str]:
        peers_by_join_order = sorted(self.roster, key=lambda uid: self.roster[uid].id)
        return ["__self__", *peers_by_join_order]

    def compose_frame(self, self_frame: np.ndarray | None) -> str:
        ordered = self._ordered_tile_user_ids()
        cols, rows = grid_dimensions(len(ordered))
        term_cols, term_rows = output_size(self.target_width)
        tile_w = max(1, term_cols // cols)
        tile_h = max(2, ((term_rows // rows) // 2) * 2)

        FAILED_TILE_RGB = (139, 0, 0)  # dark red: visually distinct from a black "no frame yet" tile
        tiles = []
        for user_id in ordered:
            if user_id in self.failed_peers:
                pixels = np.tile(np.array(FAILED_TILE_RGB, dtype=np.uint8), (tile_h, tile_w, 1))
            else:
                frame = self_frame if user_id == "__self__" else self.video_tiles.get(user_id)
                pixels = (
                    np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
                    if frame is None
                    else cv2.resize(frame, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
                )
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
        with live_screen():
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_call_render.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add termcall/call.py tests/test_call_render.py
git commit -m "feat: add grid render loop and CallSession.run entrypoint"
```

---

### Task 22: Wire `room create`/`room join` commands and error handling

**Files:**
- Modify: `main.py`
- Test: `tests/test_cli_room.py`

**Interfaces:**
- Consumes: everything from Tasks 9–21.
- Produces: `termcall room create [-d DEVICE] [-w WIDTH]`, `termcall room join CODE [-d DEVICE] [-w WIDTH]`, mapping every condition in design §10's error table to the stated user-facing message.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_room.py
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from main import cli
from termcall.media import DeviceError
from termcall.rooms import Room, RoomFullError, RoomNotJoinableError
from termcall.session import Session
from termcall.supabase_client import SessionExpiredError


def _patched_session():
    return patch(
        "termcall.supabase_client.authenticated_async_client",
        new=AsyncMock(return_value=(MagicMock(), Session("a", "r", "me@example.com"))),
    )


def test_room_join_reports_not_joinable():
    with _patched_session(), \
         patch("termcall.rooms.join_room", new=AsyncMock(side_effect=RoomNotJoinableError("room not joinable"))), \
         patch("main._resolve_user_id", new=AsyncMock(return_value="me")):
        result = CliRunner().invoke(cli, ["room", "join", "BADCODE"])
    assert result.exit_code != 0
    assert "no such room, or it has ended." in result.output


def test_room_join_reports_room_full():
    with _patched_session(), \
         patch("termcall.rooms.join_room", new=AsyncMock(side_effect=RoomFullError("room full"))), \
         patch("main._resolve_user_id", new=AsyncMock(return_value="me")):
        result = CliRunner().invoke(cli, ["room", "join", "FULLCODE"])
    assert result.exit_code != 0
    assert "room is full (max 4 participants)." in result.output


def test_room_create_reports_expired_session():
    with patch(
        "termcall.supabase_client.authenticated_async_client",
        new=AsyncMock(side_effect=SessionExpiredError("expired")),
    ):
        result = CliRunner().invoke(cli, ["room", "create"])
    assert result.exit_code != 0
    assert "termcall login" in result.output


def test_room_create_reports_camera_failure():
    room = Room(id="room-1", code="ABC123", host_id="me", status="open")
    with _patched_session(), \
         patch("termcall.rooms.create_room", new=AsyncMock(return_value=room)), \
         patch("main._resolve_user_id", new=AsyncMock(return_value="me")), \
         patch("termcall.media.open_camera", side_effect=DeviceError("Could not open camera device 0.")):
        result = CliRunner().invoke(cli, ["room", "create"])
    assert result.exit_code != 0
    assert "Could not open camera device 0." in result.output


def test_room_create_exits_non_zero_when_call_session_reports_a_fatal_ice_failure():
    from termcall.rooms import Member

    room = Room(id="room-1", code="ABC123", host_id="me", status="open")
    fake_cap = MagicMock()
    fake_cap.read.return_value = (True, MagicMock())
    fake_stream = MagicMock()
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    fake_session = MagicMock()
    fake_session.run = AsyncMock()
    fake_session.fatal_error = (
        "Could not establish a direct connection with your peer "
        "(NAT traversal failed, no relay server configured)"
    )
    fake_channel = MagicMock()
    fake_channel.unsubscribe = AsyncMock()

    with _patched_session(), \
         patch("termcall.rooms.create_room", new=AsyncMock(return_value=room)), \
         patch("main._resolve_user_id", new=AsyncMock(return_value="me")), \
         patch("termcall.media.open_camera", return_value=fake_cap), \
         patch("termcall.media.open_microphone", return_value=fake_stream), \
         patch(
             "termcall.rooms.fetch_active_roster",
             new=AsyncMock(return_value=[Member(id=1, room_id="room-1", user_id="me", joined_at="t", left_at=None)]),
         ), \
         patch(
             "termcall.signaling.subscribe_room_members", new=AsyncMock(return_value=([], fake_channel))
         ), \
         patch(
             "termcall.signaling.subscribe_signals", new=AsyncMock(return_value=([], fake_channel))
         ), \
         patch("main.CallSession", return_value=fake_session):
        result = CliRunner().invoke(cli, ["room", "create"])

    assert result.exit_code != 0
    assert "NAT traversal failed" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_room.py -v`
Expected: FAIL with `Error: No such command 'room'.`

- [ ] **Step 3: Wire the commands into `main.py`**

```python
# main.py — add these imports at the top
import asyncio

from termcall import media, rooms, signaling, supabase_client
from termcall.call import CallSession
from termcall.media import LocalAudioTrack, LocalVideoTrack

# main.py — add after the existing auth commands
@cli.group("room")
def room_group() -> None:
    """Create or join a call room."""


@room_group.command("create")
@click.option("-d", "--device", default=0, show_default=True, help="Camera device index.")
@click.option("-w", "--width", "target_width", type=int, default=None, help="Grid output width in terminal columns.")
def room_create_cmd(device: int, target_width: int | None) -> None:
    """Create a room and wait for others to join."""
    asyncio.run(_room_create_async(device, target_width))


@room_group.command("join")
@click.argument("code")
@click.option("-d", "--device", default=0, show_default=True, help="Camera device index.")
@click.option("-w", "--width", "target_width", type=int, default=None, help="Grid output width in terminal columns.")
def room_join_cmd(code: str, device: int, target_width: int | None) -> None:
    """Join an existing room by its code."""
    asyncio.run(_room_join_async(code, device, target_width))


async def _require_async_session():
    try:
        return await supabase_client.authenticated_async_client()
    except SessionExpiredError:
        raise click.ClickException("Session expired. Run `termcall login` again.") from None


async def _resolve_user_id(client) -> str:
    user = await client.auth.get_user()
    return user.user.id


async def _room_create_async(device: int, target_width: int | None) -> None:
    client, _session = await _require_async_session()
    my_user_id = await _resolve_user_id(client)
    room = await rooms.create_room(client, my_user_id)
    click.echo(f"Room created. Share this code: {room.code}")
    await _run_call(client, room, my_user_id, device, target_width)


async def _room_join_async(code: str, device: int, target_width: int | None) -> None:
    client, _session = await _require_async_session()
    my_user_id = await _resolve_user_id(client)
    try:
        room = await rooms.join_room(client, code)
    except rooms.RoomNotJoinableError:
        raise click.ClickException("no such room, or it has ended.") from None
    except rooms.RoomFullError:
        raise click.ClickException("room is full (max 4 participants).") from None
    await _run_call(client, room, my_user_id, device, target_width)


async def _run_call(client, room, my_user_id: str, device: int, target_width: int | None) -> None:
    try:
        cap = media.open_camera(device)
    except media.DeviceError as err:
        raise click.ClickException(str(err)) from None

    try:
        loop = asyncio.get_event_loop()
        mic_queue: asyncio.Queue = asyncio.Queue()
        try:
            stream = media.open_microphone(mic_queue, loop)
        except media.DeviceError as err:
            raise click.ClickException(str(err)) from None

        with stream:
            roster = await rooms.fetch_active_roster(client, room.id)
            my_member = next(m for m in roster if m.user_id == my_user_id)

            local_video = LocalVideoTrack(frame_source=lambda: cap.read()[1], fps=20.0)
            local_audio = LocalAudioTrack(queue=mic_queue)

            async def send_signal(recipient_id: str, kind: str, payload: dict) -> None:
                await signaling.insert_signal(
                    client,
                    room_id=room.id,
                    sender_id=my_user_id,
                    recipient_id=recipient_id,
                    kind=kind,
                    payload=payload,
                )

            async def do_leave() -> None:
                await rooms.leave_room(client, room.id)

            session = CallSession(
                my_user_id=my_user_id,
                my_member_id=my_member.id,
                local_video_track=local_video,
                local_audio_track=local_audio,
                camera_frame_source=lambda: cap.read()[1],
                send_signal=send_signal,
                leave_room=do_leave,
                target_width=target_width,
            )

            for member in roster:
                if member.user_id != my_user_id:
                    await session.handle_member_joined(member)

            _members_backlog, members_channel = await signaling.subscribe_room_members(
                client, room.id, session.handle_member_joined
            )
            signals_backlog, signals_channel = await signaling.subscribe_signals(
                client, room.id, my_user_id, session.handle_signal
            )
            for signal in signals_backlog:
                await session.handle_signal(signal)

            try:
                await session.run()
            finally:
                await members_channel.unsubscribe()
                await signals_channel.unsubscribe()

            if session.fatal_error is not None:
                raise click.ClickException(session.fatal_error)
    finally:
        cap.release()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_room.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full unit test suite (integration tests excluded by default)**

Run: `uv run pytest -v`
Expected: PASS, every test from Tasks 1–22 except `tests/integration/` (skipped) and `tests/test_peers_integration.py` if it's slow — both are fine to include; only genuinely network-dependent integration tests skip.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_cli_room.py
git commit -m "feat: wire room create/join commands with design §10 error handling"
```

---

### Task 23: README, manual verification checklist, migration sign-off

**Files:**
- Modify: `README.md`

**Interfaces:**
- No new code interfaces — this is documentation plus the final spec-compliance smoke test.

- [ ] **Step 1: Write `README.md`**

```markdown
# termcall

Peer-to-peer terminal video calling. Audio/video goes directly between
participants over WebRTC; Supabase is used only for auth and call signaling.

## Setup

```bash
uv sync
export SUPABASE_URL=https://<project>.supabase.co
export SUPABASE_ANON_KEY=<anon-key>
```

Apply the database schema to your Supabase project (or a local instance via
`supabase start`):

```bash
supabase db push   # or: supabase db reset, for a local instance
```

## Usage

```bash
termcall signup                 # create an account
termcall login                  # sign in, session stored in the OS keyring
termcall whoami                 # confirm who's logged in
termcall room create            # create a room, print a shareable code
termcall room join <code>       # join an existing room
termcall logout                 # clear the local session
termcall preview                # local-only webcam preview, no auth/network
```

In a call: `q` / `Esc` / `Ctrl-C` to leave, `m` to mute your mic, `v` to stop
sending video.

Rooms hold up to 4 participants (mesh of direct WebRTC connections). STUN
only — no TURN relay — so calls between peers behind symmetric NAT may fail
to connect; see design §10.

## Manual verification (full call flow)

Real peer-to-peer audio/video between separate processes isn't meaningfully
unit-testable (design §12). Before shipping a change that touches
`termcall/call.py`, `termcall/peers.py`, `termcall/media.py`, or
`termcall/signaling.py`, run this checklist:

1. Run `termcall signup` + `termcall login` for 2–4 separate accounts (in
   separate terminals, or on separate machines).
2. `termcall room create` in the first terminal; note the printed code.
3. `termcall room join <code>` in each other terminal.
4. Confirm each terminal's grid grows as each participant joins (1×1 → 1×2 →
   2×2), and that every tile — including your own — shows live video.
5. Confirm audio is audible in both directions between every pair.
6. Press `m` in one terminal; confirm the others stop hearing that
   participant, and pressing `m` again restores audio.
7. Press `v` in one terminal; confirm that tile goes black in every other
   terminal without dropping the connection, and pressing `v` again
   restores video.
8. Press `q` in one non-last terminal; confirm its process exits, its tile
   disappears everywhere else, and the remaining participants' call
   continues uninterrupted.
9. Leave everyone; confirm the room's `status` flips to `ended` in the
   `rooms` table (or that a fresh `termcall room join <code>` afterward
   reports "no such room, or it has ended.").
```

- [ ] **Step 2: Run the full test suite one final time**

Run: `uv run pytest -v`
Expected: PASS, all unit tests from Tasks 1–22 (integration tests under `tests/integration/` skip without `SUPABASE_URL`).

- [ ] **Step 3: Perform the manual verification checklist**

Run through README's "Manual verification" steps 1–9 with 2 terminals at minimum (ideally 3–4 to exercise the mesh grid growth beyond n=2).
Expected: every step's stated confirmation holds.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add usage instructions and manual call-flow verification checklist"
```

