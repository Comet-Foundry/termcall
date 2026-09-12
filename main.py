"""termcall CLI entrypoint: a click group wiring auth, room, and preview commands."""

import asyncio

import click

from termcall import auth, media, preview as preview_mod, rooms, signaling, supabase_client
from termcall.call import CallSession
from termcall.media import LocalAudioTrack, LocalVideoTrack
from termcall.supabase_client import SessionExpiredError


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

            members_channel = None
            signals_channel = None
            try:
                members_backlog, members_channel = await signaling.subscribe_room_members(
                    client, room.id, session.handle_member_joined
                )
                signals_backlog, signals_channel = await signaling.subscribe_signals(
                    client, room.id, my_user_id, session.handle_signal
                )
                for member in members_backlog:
                    if member.user_id != my_user_id and member.user_id not in session.roster:
                        await session.handle_member_joined(member)
                for signal in signals_backlog:
                    await session.handle_signal(signal)

                await session.run()
            finally:
                if members_channel is not None:
                    await members_channel.unsubscribe()
                if signals_channel is not None:
                    await signals_channel.unsubscribe()
                await rooms.leave_room(client, room.id)

            if session.fatal_error is not None:
                raise click.ClickException(session.fatal_error)
    finally:
        cap.release()


if __name__ == "__main__":
    cli()
