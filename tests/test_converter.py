import os
import pytest
import subprocess
import json
from unittest.mock import MagicMock, mock_open
from core.converter import AudioConverter, resolve_cover_path

# --- Setup & Fixtures ---

@pytest.fixture
def converter():
    return AudioConverter(logger=MagicMock())

@pytest.fixture
def mock_process(monkeypatch):
    """Provides a mocked FFmpeg process that simulates stdout progress and successful exit."""
    process = MagicMock()
    # Simulate FFmpeg output lines, including a valid progress update and garbage data
    process.stdout = ["random ffprobe header", "out_time_us=5000000", "out_time_us=N/A"]
    process.returncode = 0
    process.poll.return_value = 0  # Simulates immediate completion for while loops
    
    mock_runner = MagicMock(return_value=process)
    monkeypatch.setattr("core.converter.ProcessRunner.run_async", mock_runner)
    return process

# --- Helper Tests ---

def test_resolve_cover_path(monkeypatch):
    # Test 1: Missing base path
    assert resolve_cover_path(None, "123") is None
    
    # Test 2: Padded ASIN match
    def mock_exists_padded(p): return "0000000123.jpg" in str(p)
    monkeypatch.setattr(os.path, "exists", mock_exists_padded)
    assert resolve_cover_path("/fake/cover.jpg", "123").endswith("0000000123.jpg")
    
    # Test 3: Generic fallback match
    def mock_exists_generic(p): return str(p).endswith("folder.jpg")
    monkeypatch.setattr(os.path, "exists", mock_exists_generic)
    assert resolve_cover_path("/fake/cover.jpg", "123").endswith("folder.jpg")
    
    # Test 4: Complete miss
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    assert resolve_cover_path("/fake/cover.jpg", "123") is None

# --- Core Converter Tests ---

def test_get_metadata_and_chapters(converter, monkeypatch):
    mock_run = MagicMock()
    monkeypatch.setattr("core.converter.ProcessRunner.run_blocking", mock_run)

    win_output = {
        "format": {
            "duration": "3600.5",
            "tags": {"title": "Windows Book", "artist": "Bob"}
        },
        "chapters": [{"id": 0, "tags": {"title": "Chapter 1"}}]
    }
    mock_run.return_value.stdout = json.dumps(win_output)
    
    data = converter.get_metadata_and_chapters("/fake_win.m4b")
    assert data["format"]["tags"]["title"] == "Windows Book"
    assert data["format"]["duration"] == 3600.5
    assert data["chapters"][0]["tags"]["title"] == "Chapter 1"

    mac_output = {
        "format": {
            "duration": "N/A",
            "tags": {"TITLE": "Mac Book", "ARTIST": "Alice", "ALBUM": "Mac Album"}
        },
        "chapters": [{"id": 0, "tags": {"TITLE": "Prologue"}}]
    }
    mock_run.return_value.stdout = json.dumps(mac_output)
    
    data = converter.get_metadata_and_chapters("/fake_mac.mp3")

    assert "title" in data["format"]["tags"]
    assert data["format"]["tags"]["title"] == "Mac Book"
    assert "artist" in data["format"]["tags"]

    assert data["format"]["duration"] == 0.0

    assert "title" in data["chapters"][0]["tags"]
    assert data["chapters"][0]["tags"]["title"] == "Prologue"

    minimal_output = {
        "format": {
            "tags": {"Title": "Minimal"}
        }
    }
    mock_run.return_value.stdout = json.dumps(minimal_output)
    
    data = converter.get_metadata_and_chapters("/fake_min.m4b")
    assert data["format"]["tags"]["title"] == "Minimal"
    assert data["format"]["duration"] == 0.0  # Safe fallback applied
    assert "chapters" not in data

    mock_run.side_effect = Exception("FFprobe crashed")
    assert converter.get_metadata_and_chapters("/broken.m4b") == {}

def test_cancel_execution(converter):
    process = MagicMock()
    converter.current_process = process
    
    converter.cancel()
    
    assert converter.is_cancelled is True
    process.terminate.assert_called_once()

# --- FFmpeg Execution Tests ---

def test_convert_to_m4b_success_and_progress(converter, mock_process, monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "replace", MagicMock())
    monkeypatch.setattr(os, "remove", MagicMock())
    
    mock_progress = MagicMock()
    
    result = converter.convert_to_m4b(
        input_path="/in.aax", output_path="/out.m4b", title="Title", 
        authors="Auth", cover_path=None, drm_flags=["-activation_bytes", "deadbeef"], 
        total_duration=10.0, progress_cb=mock_progress
    )
    
    assert result is True
    # 5000000 us = 5 seconds. 5s / 10s total = 50%
    mock_progress.assert_called_with(50)

def test_convert_to_m4b_missing_ffmpeg(converter, monkeypatch):
    monkeypatch.setattr("core.converter.ProcessRunner.run_async", MagicMock(side_effect=FileNotFoundError))
    
    with pytest.raises(Exception, match="CRITICAL: FFmpeg not found"):
        converter.convert_to_m4b("/in.aax", "/out.m4b", "T", "A", None, [], 10.0)

def test_convert_to_m4b_cancellation(converter, mock_process, monkeypatch):
    monkeypatch.setattr(os, "remove", MagicMock())
    
    # Trigger cancellation mid-loop
    def side_effect_stdout():
        converter.is_cancelled = True
        yield "out_time_us=5000000"
        
    mock_process.stdout = side_effect_stdout()
    
    with pytest.raises(Exception, match="Conversion cancelled"):
        converter.convert_to_m4b("/in.aax", "/out.m4b", "T", "A", None, [], 10.0)
    
    mock_process.terminate.assert_called_once()

def test_split_into_chapters(converter, mock_process, monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os, "replace", MagicMock())
    
    chapters = [
        {"tags": {"title": "Ch 1"}, "start_time": 0, "end_time": 10},
        {"tags": {"title": "Ch 2"}, "start_time": 10, "end_time": 20}
    ]
    
    mock_progress = MagicMock()
    result = converter.split_into_chapters("/in.m4b", "/target", chapters, [], mock_progress)
    
    assert result is True
    # Should be called twice (50% and 100%)
    assert mock_progress.call_count == 2

def test_split_into_chapters_cancellation(converter, mock_process, monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    mock_remove = MagicMock()
    monkeypatch.setattr(os, "remove", mock_remove)
    monkeypatch.setattr(os, "rmdir", MagicMock())
    
    # Force cancellation on the first poll check
    mock_process.poll.return_value = None
    
    def simulate_poll():
        converter.is_cancelled = True
        return None
    mock_process.poll.side_effect = simulate_poll

    with pytest.raises(Exception, match="Chapter splitting cancelled"):
        converter.split_into_chapters("/in.m4b", "/target", [{"start_time": 0, "end_time": 10}], [])

def test_concat_to_m4b(converter, mock_process, monkeypatch):
    # Mock OS and Tempfile IO
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 1024)
    
    monkeypatch.setattr(os, "replace", MagicMock())
    monkeypatch.setattr(os, "remove", MagicMock())
    
    # Prevent FFprobe calls during test
    mock_meta = MagicMock(return_value={"format": {"duration": "100.0", "tags": {"artist": "Artist"}}})
    converter.get_metadata_and_chapters = mock_meta
    
    mock_progress = MagicMock()
    
    result = converter.concat_to_m4b(
        ["/fake/part1.mp3", "/fake/part2.mp3"], "/fake/out.m4b", 
        title="Merged Book", progress_cb=mock_progress
    )
    
    assert result is True