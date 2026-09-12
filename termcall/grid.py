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
