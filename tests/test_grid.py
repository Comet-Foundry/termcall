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
