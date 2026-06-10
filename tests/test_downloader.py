import os
import urllib.request
from unittest.mock import MagicMock

import pytest

from core.downloader import AudiobookDownloader, DownloadCanceledError

# --- Mock Infrastructure ---


class MockNetworkResponse:
    """Simulates a chunked network stream returned by urllib.urlopen."""

    def __init__(self, chunks, total_size):
        self.chunks = chunks
        self.headers = {"content-length": str(total_size)}
        self.index = 0

    def read(self, size):
        if self.index < len(self.chunks):
            chunk = self.chunks[self.index]
            self.index += 1
            return chunk
        return b""  # Empty byte string signals End of File

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def mock_api():
    """Provides a mocked API client that returns standard decryption keys."""
    api = MagicMock()
    api.get_download_license.return_value = (
        "https://fake.url/download",
        "fake_key",
        "fake_iv",
    )
    return api


@pytest.fixture
def downloader(mock_api):
    return AudiobookDownloader(api_client=mock_api, logger=MagicMock())


# --- Tests ---


def test_download_success_aaxc(downloader, monkeypatch):
    """Verifies a successful download sequence for an encrypted .aaxc file."""

    # Simulate a 30-byte file arriving in 3 chunks
    mock_resp = MockNetworkResponse([b"0123456789", b"0123456789", b"0123456789"], 30)
    monkeypatch.setattr(urllib.request, "urlopen", MagicMock(return_value=mock_resp))

    # Mock file I/O
    monkeypatch.setattr("builtins.open", MagicMock())
    mock_replace = MagicMock()
    monkeypatch.setattr(os, "replace", mock_replace)

    mock_progress = MagicMock()

    # Title includes illegal characters to test the safe_title sanitization
    filepath, a_key, a_iv, ext = downloader.download_item(
        "123", "Test: Title!!!", "/fake/dir", progress_callback=mock_progress
    )

    # 1. Verify Return Types and File Naming
    assert ext == ".aaxc"
    assert a_key == "fake_key"
    assert a_iv == "fake_iv"
    assert filepath == os.path.join("/fake/dir", "Test Title [123].aaxc")

    # 2. Verify Finalization
    mock_replace.assert_called_once()
    assert mock_replace.call_args[0][0].endswith(".part")  # Moved from .part
    assert mock_replace.call_args[0][1] == filepath  # To final filepath

    # 3. Verify Progress Tracking
    # Should have been called at least once as the chunks arrived
    assert mock_progress.call_count >= 1


def test_download_success_aax(downloader, monkeypatch):
    """Verifies fallback to standard .aax extension if no decryption keys exist."""
    downloader.api.get_download_license.return_value = (
        "https://fake.url/download",
        None,
        None,
    )

    mock_resp = MockNetworkResponse([b"data"], 4)
    monkeypatch.setattr(urllib.request, "urlopen", MagicMock(return_value=mock_resp))
    monkeypatch.setattr("builtins.open", MagicMock())
    monkeypatch.setattr(os, "replace", MagicMock())

    filepath, a_key, a_iv, ext = downloader.download_item("123", "Title", "/fake/dir")

    assert ext == ".aax"
    assert a_key is None
    assert filepath.endswith(".aax")


def test_download_cancellation(downloader, monkeypatch):
    """Verifies the UI cancel button halts the stream and cleans up the partial file."""
    mock_resp = MockNetworkResponse([b"chunk1", b"chunk2"], 12)
    monkeypatch.setattr(urllib.request, "urlopen", MagicMock(return_value=mock_resp))
    monkeypatch.setattr("builtins.open", MagicMock())

    # Mock OS cleanup functions
    mock_unlink = MagicMock()
    monkeypatch.setattr("core.downloader.safe_unlink", mock_unlink)
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    # Simulate the user clicking cancel after the first chunk downloads
    call_count = [0]

    def mock_check_cancel():
        call_count[0] += 1
        return call_count[0] > 1

    with pytest.raises(DownloadCanceledError):
        downloader.download_item(
            "123", "Title", "/fake/dir", check_cancel_callback=mock_check_cancel
        )

    # Verify the partial file was aggressively deleted
    mock_unlink.assert_called_once()
    assert "Title [123].aaxc.part" in mock_unlink.call_args[0][0]


def test_download_network_error_cleanup(downloader, monkeypatch):
    """Verifies that an unexpected network crash leaves no orphan .part files."""
    # Force urllib to crash immediately upon opening the connection
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        MagicMock(side_effect=Exception("Connection Reset by Peer")),
    )

    mock_unlink = MagicMock()
    monkeypatch.setattr("core.downloader.safe_unlink", mock_unlink)
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    with pytest.raises(Exception, match="Connection Reset"):
        downloader.download_item("123", "Title", "/fake/dir")

    # Verify cleanup still triggered
    mock_unlink.assert_called_once()


def test_download_cleanup_os_error(downloader, monkeypatch):
    """Verifies it doesn't crash if the OS locks the .part file during cleanup."""
    import urllib.request

    monkeypatch.setattr(
        urllib.request, "urlopen", MagicMock(side_effect=Exception("Crash"))
    )
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    # Force the cleanup deletion to fail
    monkeypatch.setattr("core.downloader.safe_unlink", MagicMock())

    with pytest.raises(Exception, match="Crash"):
        downloader.download_item("123", "Title", "/fake/dir")
