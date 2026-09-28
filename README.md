# termcall

Peer-to-peer terminal video calling. Audio/video goes directly between
participants over WebRTC; Supabase is used only for auth and call signaling.

## Setup

```bash
uv sync
export SUPABASE_URL=https://bmrkludoecyciwpljfve.supabase.co
export SUPABASE_ANON_KEY=sb_publishable_t7YH_XOTkf3TRIaIOb9TGQ_IJv-PZmD
```

The schema (`supabase/migrations/`) is already applied to this project. To
point at a different Supabase project instead (e.g. a local instance via
`supabase start`), export its URL/anon key and run `supabase link
--project-ref <ref> && supabase db push` (or `supabase db reset` for local).

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

`termcall/peers.py`/`termcall/signaling.py` reaching a real, negotiated WebRTC
connection between two OS processes is covered by
`tests/integration/test_two_process_call.py`, which spawns two real `python`
processes against a local Supabase instance and swaps synthetic media for
camera/microphone hardware so it's deterministic (run it with `supabase start`
and `SUPABASE_URL`/`SUPABASE_ANON_KEY` pointed at `http://127.0.0.1:54321`).
Actual camera/microphone hardware, the terminal UI, and multi-peer (3-4
participant) grids are not — before shipping a change that touches
`termcall/call.py`, `termcall/peers.py`, `termcall/media.py`, or
`termcall/signaling.py`, also run this manual checklist:

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
