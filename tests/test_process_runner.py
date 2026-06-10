import os
import subprocess
from unittest.mock import MagicMock

from core.utils.process_runner import ProcessRunner


def test_get_creation_flags_windows(monkeypatch):
    """Verifies Windows gets the CREATE_NO_WINDOW flag to hide the terminal."""
    monkeypatch.setattr(os, "name", "nt")

    # Safely mock the flag in case the test suite is running on Linux/Mac
    expected_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    if not hasattr(subprocess, "CREATE_NO_WINDOW"):
        monkeypatch.setattr(
            subprocess, "CREATE_NO_WINDOW", expected_flag, raising=False
        )

    assert ProcessRunner.get_creation_flags() == expected_flag


def test_get_creation_flags_unix(monkeypatch):
    """Verifies Unix/Mac systems return 0 for creation flags."""
    monkeypatch.setattr(os, "name", "posix")
    assert ProcessRunner.get_creation_flags() == 0


def test_run_blocking_pops_kwargs_and_executes(monkeypatch):
    """Verifies standard arguments are stripped and hardcoded safety defaults are applied."""
    mock_run = MagicMock()
    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(ProcessRunner, "get_creation_flags", lambda: 999)

    ProcessRunner.run_blocking(
        ["echo", "test"],
        text=False,  # Should be popped and replaced
        encoding="ascii",  # Should be popped and replaced
        creationflags=123,  # Should be popped and replaced
        check=True,
        timeout=10,  # Should be passed through cleanly
    )

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args

    assert args[0] == ["echo", "test"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["creationflags"] == 999
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 10


def test_run_async_executes(monkeypatch):
    """Verifies background process spawning passes the correct default pipes."""
    mock_popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(ProcessRunner, "get_creation_flags", lambda: 888)

    ProcessRunner.run_async(["ping", "localhost"], custom_arg="passthrough")

    mock_popen.assert_called_once()
    args, kwargs = mock_popen.call_args

    assert args[0] == ["ping", "localhost"]
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["creationflags"] == 888
    assert kwargs["custom_arg"] == "passthrough"
