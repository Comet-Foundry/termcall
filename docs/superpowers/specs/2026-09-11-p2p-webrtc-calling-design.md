# P2P WebRTC Calling CLI — Design

Status: approved for planning
Date: 2026-09-11

## 1. Summary

Transform `termcall` from a local-only webcam-to-terminal preview tool into a
peer-to-peer audio/video calling CLI. Two users authenticate against Supabase,
find each other via a room code or email, exchange WebRTC SDP/ICE signaling
through a Supabase Postgres table + Realtime subscription, then carry audio
and video directly between their two machines over a WebRTC
`RTCPeerConnection` (no media ever touches Supabase).

## 2. Goals

- Email/password authentication (Supabase Auth), session persisted in the OS
  keyring between CLI invocations.
- Place a 1:1 call either by a shareable room code or directly by the other
  user's email.
- Real bidirectional audio + video over WebRTC, NAT-traversed via public
  STUN.
- Render the remote peer's video in the terminal using the existing
  half-block ANSI renderer; show a small sixel self-preview in the
  bottom-left corner when the terminal supports it, and simply omit it
  otherwise.
- Keep the existing local-only `termcall preview` command working unchanged,
  with no auth/network dependency.

## 3. Non-goals (v1)

- Group calls (>2 participants). The room/signal schema and connection model
  are explicitly 1:1; extending to mesh calling is a separate future design.
- TURN relay / symmetric-NAT fallback. STUN-only; documented limitation.
- Any form of text chat or WebRTC data channel.
- SSH-keypair or magic-link auth (README's original Firebase-era plan is
  superseded by Supabase email/password auth).
- Automatic background/service-mode "always listening for calls" daemon —
  incoming calls are discovered by polling (`termcall calls`), not pushed
  while the CLI is closed.

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
  caller_id   uuid not null references profiles(id),
  invitee_id  uuid references profiles(id),
  status      text not null default 'pending'
              check (status in ('pending', 'active', 'ended')),
  created_at  timestamptz not null default now(),
  ended_at    timestamptz
);

create table signals (
  id         bigint generated always as identity primary key,
  room_id    uuid not null references rooms(id) on delete cascade,
  sender_id  uuid not null references profiles(id),
  kind       text not null check (kind in ('offer', 'answer', 'ice')),
  payload    jsonb not null,
  created_at timestamptz not null default now()
);
```

### 4.1 Profile provisioning

A trigger on `auth.users` (`after insert`) inserts the matching `profiles`
row, so `termcall call <email>` can resolve an email to a user id under RLS
without a service-role key.

### 4.2 Room join concurrency

Joining an *open* room (created via `termcall room create`, `invitee_id`
initially null) must be atomic to avoid two people joining the same code. A
`security definer` RPC handles this:

```sql
create function join_room(p_code text) returns rooms
language plpgsql security definer as $$
declare r rooms;
begin
  update rooms set invitee_id = auth.uid()
    where code = p_code and invitee_id is null and status = 'pending'
    returning * into r;
  if r.id is null then
    raise exception 'room not joinable';
  end if;
  return r;
end;
$$;
```

`termcall call <email>` skips this RPC — it inserts `rooms` directly with
`invitee_id` already set to the resolved target, so there's no race to guard
against: RLS already restricts that row to exactly `caller_id` and
`invitee_id`, so the targeted invitee is the only possible joiner.
`termcall join <room-id>` (answering such an invite) therefore performs a
plain read plus a `status` transition to `'active'` — no RPC needed, unlike
the open-room-code path.

### 4.3 RLS policies

- `profiles`: readable by any authenticated user (needed to resolve emails
  for `call <email>`); writable only by the owning row (via the trigger, not
  directly by users).
- `rooms`: select/insert/update allowed where
  `auth.uid() in (caller_id, invitee_id)`. Insert additionally requires
  `caller_id = auth.uid()`.
- `signals`: select/insert allowed where the referenced room's
  `caller_id`/`invitee_id` includes `auth.uid()`.

### 4.4 Cleanup

`termcall hangup`-equivalent logic sets `status = 'ended'`, `ended_at =
now()` on disconnect. Stale rooms/signals (ended or abandoned-pending past a
TTL, e.g. 24h) are not actively purged by a background service in v1 — this
is an accepted manual/best-effort cleanup gap, not a scheduled job, to avoid
scope creep into Edge Functions/cron for a personal calling tool.

## 5. CLI surface

Replaces the single `main` command in `main.py` with a click group. Existing
webcam-preview behavior is preserved verbatim under a `preview` subcommand.

| Command | Auth required | Behavior |
|---|---|---|
| `termcall signup` | no | Prompts email/password, calls Supabase `sign_up`. |
| `termcall login` | no | Prompts email/password, calls Supabase `sign_in_with_password`, stores access+refresh token in OS keyring. |
| `termcall logout` | yes | Deletes the keyring entry. |
| `termcall whoami` | yes | Prints the logged-in email. |
| `termcall room create` | yes | Inserts an open room, prints the room code, waits (blocks) for a peer to join, then enters the call. |
| `termcall room join <code>` | yes | Calls `join_room(code)`, then enters the call as answerer. |
| `termcall call <email>` | yes | Resolves `<email>` to a profile, inserts a room with `invitee_id` set, waits, then enters the call as offerer. |
| `termcall calls` | yes | Lists pending rooms where `invitee_id = me` and `status = 'pending'`. |
| `termcall join <room-id>` | yes | Joins a specific pending room from the `calls` list, enters the call as answerer. |
| `termcall preview` | no | Unchanged existing local-only webcam-to-terminal preview (current `main.py` behavior, moved under this subcommand name). |

Session handling: every authenticated command loads the keyring session; an
expired access token is silently refreshed via the stored refresh token; an
expired/invalid refresh token clears the keyring entry and prompts the user
to run `termcall login` again.

The offerer/answerer role is fixed by which command was run — `room create`
and `call` are always the offerer, `room join` and `join` are always the
answerer. No role negotiation is needed.

## 6. Call setup / signaling sequence

```mermaid
sequenceDiagram
    participant Caller
    participant DB as Supabase (rooms/signals)
    participant Callee

    Caller->>DB: insert room (status=pending)
    Caller->>DB: subscribe to signals where room_id=X
    Callee->>DB: join_room(code) / discovered via `termcall calls`
    Callee->>DB: subscribe to signals where room_id=X (SELECT backlog + Realtime)
    Caller->>DB: insert signal(kind=offer, payload=SDP)
    Callee->>DB: receive offer (backlog SELECT or Realtime push)
    Callee->>DB: insert signal(kind=answer, payload=SDP)
    Caller->>DB: receive answer via Realtime
    Caller->>DB: insert signal(kind=ice, payload=candidate) x N
    Callee->>DB: insert signal(kind=ice, payload=candidate) x N
    Note over Caller,Callee: aiortc ICE agent connects P2P; DTLS-SRTP established
    Note over Caller,Callee: media flows directly; DB used only for hangup/status after this point
```

Because the callee may join well after the caller created the room (the
async-inbox model chosen for this app), every subscriber first `SELECT`s
existing `signals` rows for the room, then continues consuming the Realtime
Postgres Changes stream for anything inserted after — this is precisely why
signaling uses a persisted table rather than an ephemeral Broadcast channel.

## 7. Media pipeline (aiortc)

- **Outgoing video**: a custom `aiortc.mediastreams.VideoStreamTrack`
  subclass wraps the existing `cv2.VideoCapture` loop. `cap.read()` is
  blocking, so it runs via `loop.run_in_executor`; frames are converted to
  `av.VideoFrame` for aiortc's encoder pipeline.
- **Outgoing audio**: a custom `AudioStreamTrack` backed by a
  `sounddevice.InputStream` whose callback pushes PCM chunks into an
  `asyncio.Queue` that the track drains.
- **Incoming media**: `RTCPeerConnection.on("track")` receives the remote
  video/audio tracks. Remote video frames feed the existing `frame_to_ansi`
  renderer unchanged. Remote audio frames feed a `sounddevice.OutputStream`
  for playback.
- **ICE configuration**: `RTCConfiguration(iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")])`.
  No TURN server — see §9 for the resulting failure mode.

## 8. Terminal rendering during a call

- **Remote video**: unchanged half-block ANSI algorithm from the current
  `main.py`, filling the full terminal.
- **Self-preview**: attempted via SIXEL, rendered as a small fixed-size block
  (e.g. sized to roughly 20x10 terminal cells worth of pixels) anchored to
  the bottom-left corner.
- **Capability detection**: at call start, send the DA1 query (`\x1b[c`) and
  read the response (with a short timeout, e.g. 200ms) for the sixel
  attribute (`4`) in the returned parameter list. If the query times out, the
  attribute is absent, or the sixel encoder library fails to import, the
  self-preview feature is disabled for the entire call — no retries, no
  visible error, matching the "if unsupported, no self-preview" decision.
- **Sixel encoder**: `libsixel-python` (bindings over the mature
  `saitoha/libsixel` C library), added as an optional extra
  (`termcall[sixel]`) and imported lazily behind a `try/except ImportError`.
  This is deliberately *not* a hard dependency, and deliberately not the
  pure-Python `pySixelify` project, which is a small single-file hobby
  utility whose own README lists "realtime SIXEL conversion" as a TODO —
  unsuitable to depend on for a live video feature.
- **Compositing**: each render tick draws the full remote-video block grid
  first, then (if enabled) emits the sixel self-preview via direct cursor
  positioning (`\x1b[<row>;<col>H`) and returns the cursor home afterward.
  The two draw calls never interleave mid-frame.
- **In-call keys**: `q` / `Esc` / `Ctrl-C` hang up (marks the room
  `status='ended'`, closes the `RTCPeerConnection`, exits); `m` toggles
  outgoing audio mute; `v` toggles outgoing video (stops sending frames
  without dropping the connection).

## 9. Concurrency architecture

A single asyncio event loop per call process hosts:

- aiortc's connection/media tasks,
- the async Supabase Realtime client subscription,
- terminal input, read via `loop.add_reader` on stdin in cbreak mode (the
  `select`-based approach in the current `main.py` is ported to
  `add_reader` to integrate with the asyncio loop instead of blocking
  `select.select`),
- the render loop, paced with `asyncio.sleep` at the target frame interval.

`cv2` and `sounddevice` calls that are blocking or run on their own native
callback threads are isolated from the event loop via `run_in_executor` /
thread-safe queues, matching the pattern already used for camera reads.

## 10. Error handling

| Condition | Behavior |
|---|---|
| Access token expired | Silently refreshed via stored refresh token. |
| Refresh token expired/invalid | Keyring entry cleared; user prompted to run `termcall login`. |
| Room already has two participants | `join_room` RPC raises; CLI reports "room already has two participants." |
| `call <email>` target has no profile (never signed up) | CLI resolves the email against `profiles` before inserting a room; no match → reports "no such user" and exits, no room created. |
| ICE fails to connect (`connectionState == "failed"`) | CLI prints "Could not establish a direct connection with your peer (NAT traversal failed, no relay server configured)" and exits non-zero. Documented v1 limitation from the STUN-only decision. |
| Peer hangs up | Local side, subscribed to room status changes, observes `status='ended'`, tears down the peer connection, prints "Peer ended the call," exits 0. |
| Camera/mic unavailable | Same `click.ClickException` pattern as the current `main.py`, extended to `sounddevice` device-open failures. |

## 11. Dependencies

Added to `pyproject.toml`:

- `supabase` — Supabase Python client (auth + Postgres + Realtime).
- `aiortc` — WebRTC peer connection, media tracks, SDP/ICE.
- `av` — pulled in transitively by `aiortc`; used directly for frame
  conversion.
- `sounddevice` — microphone capture / speaker playback.
- `keyring` — OS-backed session token storage.

Optional extra `termcall[sixel]`:

- `libsixel-python` — self-preview rendering; absence degrades gracefully
  per §8.

`opencv-python-headless` and `numpy` (existing dependencies) are retained
unchanged.

## 12. Testing strategy

- **Unit-testable in isolation**: room-code generation, sixel
  capability-response parsing, signaling payload shaping/parsing
  (offer/answer/ICE JSON shapes), keyring session read/write round-trip.
- **RLS policies**: integration-tested against a local Supabase instance
  (`supabase start`), asserting two distinct authenticated users can only
  see/modify rows they're a participant in.
- **Full call flow**: two real peer processes exchanging real audio/video
  over a real network is not meaningfully automatable. Verification is
  manual — run both CLI roles locally (or across two machines), confirm
  actual audio/video flow — the same verification approach already used for
  the existing `main.py` preview feature.

## 13. Migration from current `main.py`

- Current `frame_to_ansi`, `terminal_size`, `output_size`, `raw_terminal`,
  `live_screen`, and the render loop's core logic are reused, not rewritten,
  for both `preview` and the in-call remote-video rendering.
- `main.py`'s single `@click.command()` becomes a `@click.group()` with the
  commands in §5; the existing command body becomes the `preview`
  subcommand, unchanged in behavior.
