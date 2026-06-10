import logging
import os
from unittest.mock import MagicMock

import pytest

from core.utils.logger import CallableLogger, setup_logger

# --- Fixtures ---


@pytest.fixture(autouse=True)
def reset_logger():
    """
    Crucial: The logging module tracks loggers globally.
    We must clear the handlers before and after every test
    so they don't bleed into each other and duplicate logs!
    """
    logger = logging.getLogger("TomeBox")
    logger.handlers.clear()
    yield
    logger.handlers.clear()


# --- CallableLogger Tests ---


def test_callable_logger_default_call():
    """Verifies that calling the object directly routes to .info()"""
    mock_std_logger = MagicMock()
    app_logger = CallableLogger(mock_std_logger)

    app_logger("This is a standard info message")

    mock_std_logger.info.assert_called_once_with("This is a standard info message")


def test_callable_logger_error_routing():
    """Verifies error routing and kwarg passing."""
    mock_std_logger = MagicMock()
    app_logger = CallableLogger(mock_std_logger)

    app_logger.error("Something broke", exc_info=True)

    mock_std_logger.error.assert_called_once_with("Something broke", exc_info=True)


def test_callable_logger_exception_routing():
    """Verifies exception routing."""
    mock_std_logger = MagicMock()
    app_logger = CallableLogger(mock_std_logger)

    app_logger.exception("Catastrophic failure")

    mock_std_logger.exception.assert_called_once_with("Catastrophic failure")


def test_callable_logger_getattr_delegation():
    """Verifies that unmapped methods (like .debug) pass cleanly through to the underlying logger."""
    mock_std_logger = MagicMock()
    app_logger = CallableLogger(mock_std_logger)

    # .debug isn't explicitly defined in CallableLogger, so __getattr__ should catch it
    app_logger.debug("A debug message")

    mock_std_logger.debug.assert_called_once_with("A debug message")


# --- setup_logger Tests ---


def test_setup_logger_creates_directories_and_files(tmp_path):
    """Verifies the logger correctly creates the /logs directory and writes to disk."""
    base_dir = str(tmp_path)

    # Initialize the logger
    app_logger = setup_logger(base_dir)

    # Check directory
    log_dir = os.path.join(base_dir, "logs")
    assert os.path.exists(log_dir)

    # Write a test log to force the file creation
    app_logger("Test log entry")

    # Check file
    log_file = os.path.join(log_dir, "tomebox.log")
    assert os.path.exists(log_file)

    # Verify content
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "[INFO] Test log entry" in content


def test_setup_logger_levels(tmp_path):
    """Verifies debug_mode toggles the logging level correctly."""
    base_dir = str(tmp_path)

    # 1. Normal Mode
    normal_logger = setup_logger(base_dir, debug_mode=False)
    assert normal_logger.level == logging.INFO

    # Clear handlers manually to simulate a fresh boot for the next setup
    logging.getLogger("TomeBox").handlers.clear()

    # 2. Debug Mode
    debug_logger = setup_logger(base_dir, debug_mode=True)
    assert debug_logger.level == logging.DEBUG


def test_setup_logger_prevents_duplicate_handlers(tmp_path):
    """Verifies that calling setup_logger multiple times doesn't stack handlers."""
    base_dir = str(tmp_path)

    # First call
    setup_logger(base_dir)
    root_logger = logging.getLogger("TomeBox")
    initial_count = len(root_logger.handlers)

    # Ensure it actually added handlers (likely 2: File and Stream)
    assert initial_count > 0

    # Second call (Simulating another module importing and calling setup)
    setup_logger(base_dir)

    # The count should remain EXACTLY the same to prevent double-printing logs
    assert len(root_logger.handlers) == initial_count
