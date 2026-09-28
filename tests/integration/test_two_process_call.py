"""Real two-OS-process integration test: spawns two separate `python` processes
that each run the production room/signaling/WebRTC call flow (see
call_peer_driver.py) against a local Supabase instance, and asserts they
establish a genuine WebRTC connection and exchange live video/audio frames
end-to-end -- an automated stand-in for the README's manual verification
checklist that doesn't depend on camera/microphone hardware or GUI permission
prompts.

Requires a local Supabase instance (`supabase start`); skipped otherwise, same
convention as test_rls.py.
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

if not os.environ.get("SUPABASE_URL"):
    pytest.skip("SUPABASE_URL not set; run `supabase start` for the two-process call test", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER_STARTUP_TIMEOUT = 15
DRIVER_TOTAL_TIMEOUT = 45


def _spawn(role: str, code: str | None = None) -> subprocess.Popen:
    args = [sys.executable, "-u", "-m", "tests.integration.call_peer_driver", "--role", role]
    if code is not None:
        args += ["--code", code]
    return subprocess.Popen(args, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _readline_with_timeout(pipe, timeout: float) -> str | None:
    sel = selectors.DefaultSelector()
    sel.register(pipe, selectors.EVENT_READ)
    try:
        if not sel.select(timeout):
            return None
    finally:
        sel.close()
    return pipe.readline()


def _parse_result(stdout: str) -> dict:
    for line in stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])
    raise AssertionError(f"driver produced no RESULT line; stdout was:\n{stdout}")


def _room_status(room_id: str) -> str:
    db_url = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    query = f"select status from public.rooms where id = '{room_id}';"
    out = subprocess.run(
        ["docker", "exec", "supabase_db_p2p-webrtc-calling", "psql", "-U", "postgres", "-t", "-A", "-c", query],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert out.returncode == 0, f"could not query room status via {db_url}: {out.stderr}"
    return out.stdout.strip()


def test_two_processes_establish_a_real_webrtc_connection():
    host_proc = _spawn("host")
    join_proc = None
    try:
        code_line = _readline_with_timeout(host_proc.stdout, timeout=DRIVER_STARTUP_TIMEOUT)
        if code_line is None or not code_line.startswith("ROOM_CODE "):
            host_proc.kill()
            _out, err = host_proc.communicate(timeout=10)
            raise AssertionError(f"host never printed a room code (got {code_line!r}); stderr:\n{err}")
        code = code_line.strip().split(" ", 1)[1]

        join_proc = _spawn("join", code=code)
        join_stdout, join_stderr = join_proc.communicate(timeout=DRIVER_TOTAL_TIMEOUT)
        host_stdout, host_stderr = host_proc.communicate(timeout=DRIVER_TOTAL_TIMEOUT)
    finally:
        for proc in (host_proc, join_proc):
            if proc is not None and proc.poll() is None:
                proc.kill()

    host_result = _parse_result(host_stdout)
    join_result = _parse_result(join_stdout)

    assert host_result["success"], f"host driver failed: {host_result}\nstderr:\n{host_stderr}"
    assert join_result["success"], f"join driver failed: {join_result}\nstderr:\n{join_stderr}"

    # Both sides independently observed the *other* participant's user id as their peer.
    assert host_result["peer_id"] == join_result["user_id"]
    assert join_result["peer_id"] == host_result["user_id"]
    assert host_result["connected"] is True
    assert join_result["connected"] is True
    assert host_result["video_frames_received"] > 0
    assert join_result["video_frames_received"] > 0
    assert host_result["audio_frames_received"] > 0
    assert join_result["audio_frames_received"] > 0

    # Decoded video actually carries the peer's synthetic color, not noise or the
    # local tile: host sends red-dominant (BGR 40,40,220 -> RGB ~220,40,40), join
    # sends blue-dominant (BGR 220,40,40 -> RGB ~40,40,220).
    host_seen_r, _host_seen_g, host_seen_b = host_result["received_mean_rgb"]
    join_seen_r, _join_seen_g, join_seen_b = join_result["received_mean_rgb"]
    assert host_seen_b - host_seen_r > 50, f"host did not receive join's blue-dominant frame: {host_result}"
    assert join_seen_r - join_seen_b > 50, f"join did not receive host's red-dominant frame: {join_result}"

    # Both participants left; the room RPC should have flipped it to 'ended'.
    assert _room_status(host_result["room_id"]) == "ended"
