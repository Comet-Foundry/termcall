"""termcall CLI entrypoint: a click group wiring auth, room, and preview commands."""

import click

from termcall import auth, preview as preview_mod
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


if __name__ == "__main__":
    cli()
