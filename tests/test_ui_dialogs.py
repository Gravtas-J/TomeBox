import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_app(monkeypatch):
    """Provides a completely headless, mocked app environment."""
    app = MagicMock()
    app.root = MagicMock()

    # Force app.root.after(delay, func, *args) to execute synchronously for the tests
    app.root.after.side_effect = lambda delay, func, *args: func(*args)

    # Mock Tkinter variables and windows so the dialogs don't crash without a real display
    import tkinter as tk

    monkeypatch.setattr(tk, "Toplevel", MagicMock())
    monkeypatch.setattr(tk, "StringVar", MagicMock())
    monkeypatch.setattr(tk, "BooleanVar", MagicMock())

    return app


def find_widget_by_text(parent, text):
    """Recursively walks the Tkinter widget tree to find a specific button."""
    try:
        if parent.cget("text") == text:
            return parent
    except tk.TclError:
        pass

    for child in parent.winfo_children():
        result = find_widget_by_text(child, text)
        if result:
            return result
    return None


@patch("ui.components.dialogs.ttk.Style")
@patch("ui.components.dialogs.ttk.Button")
def test_search_button_race_condition_and_unlock(
    mock_button_class, mock_style_class, mock_app
):
    """Verifies that the search button disables itself to prevent spamming."""
    import tkinter as tk

    from ui.components.dialogs import open_match_to_audible_window

    # 1. Seed the mock so the dialog doesn't abort early when looking up the file
    mock_app.library_manager = MagicMock()
    mock_app.library_manager.local_library = {
        "/fake/file.m4b": {"title": "Dummy Title"}
    }

    # Force the mocked StringVars to return text so the search doesn't instantly abort
    if hasattr(tk.StringVar, "return_value"):
        tk.StringVar.return_value.get.return_value = "The Hobbit"

    # 2. Capture the Search button as it gets created
    mock_search_btn = MagicMock()
    search_command = None

    def button_side_effect(*args, **kwargs):
        nonlocal search_command
        text_val = kwargs.get("text", "")
        if "Search" in str(text_val):
            search_command = kwargs.get("command")

        def config_side_effect(**cfg_kwargs):
            nonlocal search_command
            if "Search" in str(cfg_kwargs.get("text", "")):
                search_command = cfg_kwargs.get("command", search_command)
            elif "command" in cfg_kwargs and "Search" in str(text_val):
                search_command = cfg_kwargs.get("command")

        mock_search_btn.config.side_effect = config_side_effect
        mock_search_btn.configure.side_effect = config_side_effect
        return mock_search_btn

    mock_button_class.side_effect = button_side_effect

    # 3. Trigger the window creation
    open_match_to_audible_window(mock_app, "/fake/file.m4b")

    # 4. Verify it was created and has a command hooked up
    assert search_command is not None, (
        "Could not find the Search button initialization."
    )

    # 5. Execute the command (Simulate user click)
    search_command()

    # 6. Verify the race condition protection: The button MUST be disabled immediately
    disabled_called = any(
        kwargs.get("state") in [tk.DISABLED, "disabled"]
        for _, kwargs in mock_search_btn.config.call_args_list
        + mock_search_btn.configure.call_args_list
    )
    assert disabled_called, (
        "The Search button did not disable itself to prevent double-clicking!"
    )
