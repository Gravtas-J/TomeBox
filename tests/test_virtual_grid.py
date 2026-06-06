import tkinter as tk
from unittest.mock import MagicMock

import pytest

from ui.components.virtual_grid import VirtualGridView


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    yield root
    root.destroy()


@pytest.fixture
def virtual_grid(tk_root):
    mock_cache = MagicMock()
    mock_cache.get_thumbnail.return_value = ""

    grid = VirtualGridView(
        tk_root, image_cache=mock_cache, cell_width=200, cell_height=300
    )
    grid.cols = 4
    grid.x_offset = 0
    return grid


def test_grid_pool_initialization(virtual_grid):
    assert len(virtual_grid.unused_pool) == 50
    sample_cell = virtual_grid.unused_pool[0]
    assert "bg_id" in sample_cell
    assert "cover_id" in sample_cell
    assert sample_cell["is_hidden"] is True
    assert sample_cell["current_index"] is None


def test_get_index_at_math(virtual_grid):
    virtual_grid.data = [{} for _ in range(10)]
    virtual_grid.canvasx = lambda x: x
    virtual_grid.canvasy = lambda y: y

    assert virtual_grid.get_index_at(event_x=50, event_y=50) == 0
    assert virtual_grid.get_index_at(event_x=450, event_y=50) == 2
    assert virtual_grid.get_index_at(event_x=250, event_y=350) == 5
    assert virtual_grid.get_index_at(event_x=9000, event_y=9000) is None


def test_grid_set_data_triggers_repaint(virtual_grid):
    cell = virtual_grid.unused_pool.pop()
    cell["current_asin"] = "OLD_ASIN"
    virtual_grid.active_cells[0] = cell

    virtual_grid.set_data([{"asin": "NEW_ASIN"}])
    assert virtual_grid.active_cells[0]["current_asin"] == "NEW_ASIN"
