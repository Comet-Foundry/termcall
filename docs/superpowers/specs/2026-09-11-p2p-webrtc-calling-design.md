# P2P WebRTC Calling CLI — Design

Status: approved for planning
Date: 2026-09-11
Revised: 2026-09-12 — group rooms (mesh, up to 4 participants), dropped
directed invites, grid rendering replaces sixel self-preview.

## 1. Summary

Transform `termcall` from a local-only webcam-to-terminal preview tool into a
peer-to-peer audio/video calling CLI. Users authenticate against Supabase,
create or join a room by a shareable room code, exchange WebRTC SDP/ICE
signaling through a Supabase Postgres table + Realtime subscription, then
carry audio and video directly between every pair of participants' machines
over a full mesh of WebRTC `RTCPeerConnection`s (no media ever touches
Supabase). A room holds up to 4 participants — a fully connected mesh of
n(n-1)/2 direct links (6 at the 4-participant cap) — and the terminal splits
into an n-way grid, one tile per participant including yourself.

## 2. Goals

- Email/password authentication (Supabase Auth), session persisted in the OS
  keyring between CLI invocations.
- Create a room (`termcall room create`) that any number of people up to the
  4-participant cap can join over time via a shareable room code
  (`termcall room join <code>`); no other way to enter a call.
- Real bidirectional audio + video over WebRTC between every pair of
  participants in a room, NAT-traversed via public STUN.
- Render every participant's video, including your own, as an equally-sized
  tile in an n-way grid using the existing half-block ANSI renderer — no
  separate self-preview treatment.
- Keep the existing local-only `termcall preview` command working unchanged,
  with no auth/network dependency.

## 3. Non-goals (v1)

- More than 4 participants per room. The mesh model is O(n²) connections and
  encodes on a single process/core; 4 participants (6 links) is the
  documented v1 ceiling, enforced by `join_room`. Scaling further is a
  separate future design.
- Host-only "end call for everyone" control. Each participant leaves
  independently (`leave_room` for themselves only); a room only ends when
  its last active member leaves.
- TURN relay / symmetric-NAT fallback. STUN-only; documented limitation.
- Any form of text chat or WebRTC data channel.
- SSH-keypair or magic-link auth (README's original Firebase-era plan is
  superseded by Supabase email/password auth).
- Any invite/notification/discovery mechanism beyond sharing a room code
  out-of-band. There is no directed-invite flow, no pending-invite inbox, and
  no background/service-mode daemon — a room code is the only way in.

## 4. Data model (Supabase Postgres)

```sql
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
```

`host_id` is informational only (room ownership/cleanup metadata) — it
carries no special privilege in-call; see §3. `signals` rows are now
point-to-point (`sender_id` → `recipient_id`) rather than room-broadcast,
because each mesh link negotiates its own offer/answer/ICE independently.

### 4.1 Profile provisioning

A trigger on `auth.users` (`after insert`) inserts the matching `profiles`
row. `profiles` is otherwise used only as an RLS/FK anchor and to resolve
display names/emails for grid-tile captions — there is no email-based call
target in this design (see §3).

### 4.2 Room membership concurrency

Joining a room must be atomic: enforce the 4-participant cap and prevent
joining an ended room. A `security definer` RPC handles this:

```sql
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
```

`termcall room create` inserts the `rooms` row directly (`host_id =
auth.uid()`, `status = 'open'`) and then calls the same membership insert
path as `join_room` for itself, becoming active member #1.

Leaving is symmetric and also atomic, since the last member out must flip
the room to `ended`:

```sql
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

### 4.3 RLS policies

- `profiles`: readable by any authenticated user (needed to resolve display
  names/emails for room-member grid captions); writable only by the owning
  row (via the trigger, not directly by users).
- `rooms`: select allowed where `auth.uid()` is (or was) an active member of
  that room, via `room_members`. Insert allowed for any authenticated user
  with `host_id = auth.uid()` and `status = 'open'`. No direct update policy
  — status transitions only happen inside `leave_room`.
- `room_members`: select allowed where `auth.uid()` is an active member of
  the same `room_id` (lets everyone in a room see the full roster, including
  past members, for grid bookkeeping). No direct insert/update policy — rows
  are only written by `join_room`/`leave_room`.
- `signals`: select/insert allowed where `auth.uid() in (sender_id,
  recipient_id)`.

### 4.4 Cleanup

`leave_room` sets `status = 'ended'`, `ended_at = now()` on the room
automatically once its last active member departs — no separate
hangup-equivalent logic is needed. Stale rooms/signals/room_members (ended,
or abandoned-open past a TTL, e.g. 24h) are not actively purged by a
background service in v1 — this is an accepted manual/best-effort cleanup
gap, not a scheduled job, to avoid scope creep into Edge Functions/cron for
a personal calling tool.

## 5. CLI surface

Replaces the single `main` command in `main.py` with a click group. Existing
webcam-preview behavior is preserved verbatim under a `preview` subcommand.

| Command | Auth required | Behavior |
|---|---|---|
| `termcall signup` | no | Prompts email/password, calls Supabase `sign_up`. |
| `termcall login` | no | Prompts email/password, calls Supabase `sign_in_with_password`, stores access+refresh token in OS keyring. |
| `termcall logout` | yes | Deletes the keyring entry. |
| `termcall whoami` | yes | Prints the logged-in email. |
| `termcall room create` | yes | Inserts an open room, prints the room code, joins as member #1, and immediately renders the (1-tile, "waiting for others") call view — growing the grid live as people join, up to the 4-participant cap. |
| `termcall room join <code>` | yes | Calls `join_room(code)`, then joins the live grid, meshing with every existing active member per §6. |
| `termcall preview` | no | Unchanged existing local-only webcam-to-terminal preview (current `main.py` behavior, moved under this subcommand name). |

Session handling: every authenticated command loads the keyring session; an
expired access token is silently refreshed via the stored refresh token; an
expired/invalid refresh token clears the keyring entry and prompts the user
to run `termcall login` again.

There is no offerer/answerer distinction at the command level: within a
room, offerer/answerer roles are decided per pair by join order (§6), not by
which command was run.

## 6. Call setup / signaling sequence

Mesh formation rule: **for any pair of participants, whoever joined the room
earlier is the offerer toward whoever joined later.** A joining member first
`SELECT`s the current active `room_members` roster (whom to expect offers
from), then only answers; every already-active member watches
`room_members` via Realtime and, on seeing a new row, becomes the offerer to
that new member. This requires no id-comparison tie-break and produces
exactly n(n-1)/2 links with no glare.

```mermaid
sequenceDiagram
    participant A as Host A
    participant DB as Supabase (rooms/room_members/signals)
    participant B as Peer B
    participant C as Peer C

    A->>DB: insert room (status=open) + room_members(A)
    A->>DB: subscribe to room_members where room_id=X
    A->>DB: subscribe to signals where recipient_id=A
    Note over A: renders 1-tile "waiting, code X" grid

    B->>DB: join_room(code) -> insert room_members(B)
    B->>DB: SELECT active room_members backlog (sees A joined earlier)
    B->>DB: subscribe to room_members where room_id=X
    B->>DB: subscribe to signals where recipient_id=B
    A->>DB: (Realtime) observes room_members insert for B -> A is offerer
    A->>DB: insert signal(kind=offer, sender=A, recipient=B)
    B->>DB: receive offer, insert signal(kind=answer, sender=B, recipient=A)
    A->>DB: insert signal(kind=ice, sender=A, recipient=B) x N
    B->>DB: insert signal(kind=ice, sender=B, recipient=A) x N
    Note over A,B: A<->B RTCPeerConnection connects; grid grows to 2 tiles

    C->>DB: join_room(code) -> insert room_members(C)
    C->>DB: SELECT active room_members backlog (sees A, B joined earlier)
    Note over A,B: both observe the room_members insert for C -> each is offerer to C
    A->>DB: insert signal(kind=offer, sender=A, recipient=C)
    B->>DB: insert signal(kind=offer, sender=B, recipient=C)
    C->>DB: answers each offer; exchanges ICE with both A and B
    Note over A,B,C: full mesh, 3 links; grid grows to a 4-cell layout (1 blank)
    Note over A,B,C: media flows directly; DB used only for membership/status after this point
```

Because a member may join well after others are mid-call, every subscriber
first `SELECT`s existing backlog rows (`room_members` roster, `signals`
addressed to it), then continues consuming the Realtime Postgres Changes
stream for anything inserted after — this is precisely why signaling uses a
persisted table rather than an ephemeral Broadcast channel, and why
membership uses a real table rather than presence alone.

## 7. Media pipeline (aiortc)

- **Outgoing video**: a custom `aiortc.mediastreams.VideoStreamTrack`
  subclass wraps the existing `cv2.VideoCapture` loop. `cap.read()` is
  blocking, so it runs via `loop.run_in_executor`; frames are converted to
  `av.VideoFrame` for aiortc's encoder pipeline.
- **Outgoing audio**: a custom `AudioStreamTrack` backed by a
  `sounddevice.InputStream` whose callback pushes PCM chunks into an
  `asyncio.Queue` that the track drains.
- **Fan-out to the mesh**: aiortc media tracks are single-consumer, but a
  participant may have up to 3 simultaneous peer connections that each need
  every frame. The single local video/audio track is wrapped once in an
  `aiortc.contrib.media.MediaRelay`; each new peer connection gets its own
  `relay.subscribe(track)` proxy added as its outgoing track, so every peer
  receives every frame independently.
- **Incoming media**: one `RTCPeerConnection` per peer, keyed by `user_id`
  in a dict. Each connection's `on("track")` routes that peer's video/audio
  into their own grid-tile buffer. Remote video frames feed the existing
  `frame_to_ansi` renderer, invoked per-tile at the tile's resolution (§8).
  Remote audio frames feed a `sounddevice.OutputStream` for playback, mixed
  across all connected peers.
- **ICE configuration**: `RTCConfiguration(iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")])`.
  No TURN server — see §10 for the resulting failure mode.

## 8. Terminal rendering during a call

- **Grid layout**: for `n` active participants (including yourself),
  `cols = ceil(sqrt(n))`, `rows = ceil(n / cols)`. At the v1 cap this gives:
  n=1 → 1×1 (your own tile, "waiting for others — code X"); n=2 → 1×2
  side-by-side; n=3 → 2×2 with one blank cell; n=4 → full 2×2. Layout
  recomputes whenever membership changes (join or leave).
- **Tiles**: every participant, including yourself, is one grid tile
  rendered with the existing half-block ANSI algorithm, downscaled to
  `terminal_width / cols` × `terminal_height / rows` cells. There is no
  separate self-preview treatment — self is a tile like any other.
- **Compositor**: a new grid-compositor function renders each tile to an
  off-screen line buffer at its cell size, then for each grid row
  interleaves the tiles' line buffers side by side before printing, moving
  to the next grid row after. This replaces the single full-screen render
  used in the 1:1 design.
- **In-call keys**: `q` / `Esc` / `Ctrl-C` call `leave_room` for yourself
  only — tears down only your own connections and exits locally; it does
  not end the call for anyone else (see §3). `m` toggles your outgoing
  audio mute; `v` toggles your outgoing video (stops sending frames without
  dropping any connection). Both apply uniformly across every peer
  connection via the shared `MediaRelay`-backed track.

## 9. Concurrency architecture

A single asyncio event loop per call process hosts:

- aiortc's connection/media tasks for every peer connection in the mesh
  (a dict keyed by `user_id`),
- the async Supabase Realtime client subscriptions to `room_members` (for
  join/leave events that add/remove mesh links and reflow the grid) and to
  `signals` where `recipient_id = me`,
- terminal input, read via `loop.add_reader` on stdin in cbreak mode (the
  `select`-based approach in the current `main.py` is ported to
  `add_reader` to integrate with the asyncio loop instead of blocking
  `select.select`),
- the grid-compositor render loop, paced with `asyncio.sleep` at the target
  frame interval.

`cv2` and `sounddevice` calls that are blocking or run on their own native
callback threads are isolated from the event loop via `run_in_executor` /
thread-safe queues, matching the pattern already used for camera reads.

## 10. Error handling

| Condition | Behavior |
|---|---|
| Access token expired | Silently refreshed via stored refresh token. |
| Refresh token expired/invalid | Keyring entry cleared; user prompted to run `termcall login`. |
| Room code not found, or room already ended | `join_room` raises "room not joinable"; CLI reports "no such room, or it has ended." |
| Room already has 4 active participants | `join_room` raises "room full"; CLI reports "room is full (max 4 participants)." |
| ICE fails to connect to a given peer (`connectionState == "failed"`) | If other peer connections in the room are still up, that peer's tile shows a persistent "connection failed" placeholder and the call continues normally with the rest. If it was your only peer connection (e.g. a 2-person room), behavior matches the old 1:1 case: CLI prints "Could not establish a direct connection with your peer (NAT traversal failed, no relay server configured)" and exits non-zero. Documented v1 limitation from the STUN-only decision. |
| A peer leaves (`room_members.left_at` observed via Realtime) | Local side tears down just that peer's `RTCPeerConnection`, drops their tile, and reflows the grid. If it was your last remaining peer, the grid reverts to the 1-tile "waiting for others — code X" view rather than exiting — the room stays open per the join-anytime model. |
| Camera/mic unavailable | Same `click.ClickException` pattern as the current `main.py`, extended to `sounddevice` device-open failures. |

## 11. Dependencies

Added to `pyproject.toml`:

- `supabase` — Supabase Python client (auth + Postgres + Realtime).
- `aiortc` — WebRTC peer connection, media tracks, SDP/ICE, and
  `aiortc.contrib.media.MediaRelay` for fanning one local track out to
  multiple peer connections.
- `av` — pulled in transitively by `aiortc`; used directly for frame
  conversion.
- `sounddevice` — microphone capture / speaker playback.
- `keyring` — OS-backed session token storage.

`opencv-python-headless` and `numpy` (existing dependencies) are retained
unchanged. There is no SIXEL-related dependency in this design — self is a
regular grid tile, not a separately-rendered overlay.

## 12. Testing strategy

- **Unit-testable in isolation**: room-code generation, grid-layout math
  (`cols`/`rows` for n=1..4 and the resulting blank-cell handling at n=3),
  offerer/answerer determinism (given a set of join timestamps, who offers
  to whom), signaling payload shaping/parsing (offer/answer/ICE JSON shapes
  including `sender_id`/`recipient_id`), keyring session read/write
  round-trip.
- **RLS policies**: integration-tested against a local Supabase instance
  (`supabase start`), asserting up to four distinct authenticated users can
  only see/modify rows for rooms they're an active member of, that a 5th
  join attempt against a full room is rejected, and that a user cannot read
  `signals` rows addressed to someone else.
- **Full call flow**: real peer processes exchanging real audio/video over a
  real network is not meaningfully automatable. Verification is manual —
  run 2-4 CLI instances locally (or across machines), confirm actual
  audio/video flow between every pair and correct grid reflow as
  participants join/leave — the same verification approach already used for
  the existing `main.py` preview feature.

## 13. Migration from current `main.py`

- Current `frame_to_ansi`, `terminal_size`, `output_size`, `raw_terminal`,
  `live_screen`, and the render loop's core logic are reused, not rewritten,
  for `preview` and per-tile rendering in the new grid compositor.
- `main.py`'s single `@click.command()` becomes a `@click.group()` with the
  commands in §5; the existing command body becomes the `preview`
  subcommand, unchanged in behavior.
