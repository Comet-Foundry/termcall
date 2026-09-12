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
