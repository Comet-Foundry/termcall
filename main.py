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
