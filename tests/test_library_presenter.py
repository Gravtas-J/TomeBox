from unittest.mock import MagicMock

import pytest

from ui.library_presenter import LibraryPresenter


@pytest.fixture
def mock_app():
    app = MagicMock()
    app.root = MagicMock()
    app.current_view_mode = "list"
    app.ui_state.search.get.return_value = ""
    app.ui_state.filter.get.return_value = "All"
    app.ui_state.shelf_filter.get.return_value = "All Shelves"

    app.library_manager.get_view_data.return_value = ([], [])
    return app


def test_refresh_debouncer(mock_app):
    """Verifies that multiple rapid refresh requests are batched into a single deferred call."""
    presenter = LibraryPresenter(mock_app)

    presenter.refresh_library_ui()
    presenter.refresh_library_ui()
    presenter.refresh_library_ui()

    assert mock_app.root.after_cancel.call_count == 2
    mock_app.root.after.assert_called_with(150, presenter._do_refresh_library_ui)


def test_toggle_view_syncs_focus_from_list_to_grid(mock_app):
    """Verifies switching from List to Grid finds the active ASIN and scrolls to it."""
    presenter = LibraryPresenter(mock_app)

    mock_app.current_view_mode = "list"
    mock_app.library_tree.selection.return_value = ["row_1"]

    mock_app.library_tree.item.return_value = [
        "Title",
        "Auth",
        "Narr",
        "Ser",
        "Dur",
        "TARGET_ASIN",
    ]

    mock_app.grid_canvas.data = [
        {"asin": "OTHER"},
        {"asin": "TARGET_ASIN", "title": "Grid Book", "authors": "Auth"},
    ]
    mock_app.grid_canvas.cols = 4
    mock_app.grid_canvas.rows = 2

    presenter.toggle_library_view()

    assert mock_app.current_view_mode == "grid"
    assert mock_app._selected_grid_item is not None
    assert mock_app._selected_grid_item["values"][5] == "TARGET_ASIN"
    mock_app.grid_canvas.yview_moveto.assert_called()
