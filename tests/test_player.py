import os
import sys
import pytest
import subprocess
import threading
from unittest.mock import MagicMock
from core.player import AudioPlayer

# --- Fixtures ---

@pytest.fixture
def player():
    return AudioPlayer(
        logger=MagicMock(), 
        on_complete_cb=MagicMock(), 
        on_error_cb=MagicMock()
    )

# --- Playback Logic & Command Construction ---

def test_play_success_and_command_construction(player, monkeypatch):
    """Verifies FFplay command line construction including complex audio filters."""
    # 1. Mock FFprobe to pretend the file has a valid audio stream
    mock_probe_res = MagicMock()
    mock_probe_res.stdout = "aac\n"
    monkeypatch.setattr("core.player.ProcessRunner.run_blocking", MagicMock(return_value=mock_probe_res))

    # 2. Mock the async FFplay process runner
    mock_proc = MagicMock()
    mock_run_async = MagicMock(return_value=mock_proc)
    monkeypatch.setattr("core.player.ProcessRunner.run_async", mock_run_async)

    # Prevent the monitor thread from actually running
    monkeypatch.setattr(threading.Thread, "start", MagicMock())
    
    # Test Unix-style volume logic first
    monkeypatch.setattr(os, "name", "posix") 

    # Execute Play with extreme speed to test the filter chain multiplier
    res = player.play(
        filepath="/fake.m4b",
        start_time=10.5,
        remaining_duration=100.0,
        speed=3.0,  # Should trigger atempo=2.0 and atempo=1.5
        volume=80,
        voice_boost=True,
        skip_silence=True,
        drm_flags=["-activation_bytes", "deadbeef"],
        audio_device="Speakers"
    )

    assert res is True
    assert player.is_playing is True

    # Verify the exact command sent to FFplay
    mock_run_async.assert_called_once()
    cmd = mock_run_async.call_args[0][0]
    
    assert "ffplay" in cmd
    assert "-ss" in cmd
    assert "10.5" in cmd
    assert "-volume" in cmd
    assert "80" in cmd
    assert "-activation_bytes" in cmd
    assert "deadbeef" in cmd

    # Verify complex audio filters
    af_index = cmd.index("-af") + 1
    filters = cmd[af_index]
    assert "atempo=2.0" in filters
    assert "atempo=1.5" in filters
    assert "acompressor" in filters
    assert "silenceremove" in filters

    # Verify Hardware Audio Device routing
    env = mock_run_async.call_args[1]["env"]
    assert env["SDL_AUDIO_DEVICE_NAME"] == "Speakers"

def test_play_slow_speed_chaining(player, monkeypatch):
    """Verifies that speeds < 0.5 are chained correctly."""
    mock_probe_res = MagicMock()
    mock_probe_res.stdout = "aac\n"
    monkeypatch.setattr("core.player.ProcessRunner.run_blocking", MagicMock(return_value=mock_probe_res))
    
    mock_run_async = MagicMock()
    monkeypatch.setattr("core.player.ProcessRunner.run_async", mock_run_async)
    monkeypatch.setattr(threading.Thread, "start", MagicMock())

    player.play("/fake.m4b", 0, 10, 0.25, 100, False, False) # 0.25 speed
    
    cmd = mock_run_async.call_args[0][0]
    filters = cmd[cmd.index("-af") + 1]
    # To get 0.25, it should chain 0.5 and 0.5
    assert "atempo=0.5,atempo=0.5" in filters

def test_play_aborts_on_missing_audio_stream(player, monkeypatch):
    """Verifies playback bails out if FFprobe finds no audio streams (e.g. corrupt file)."""
    mock_probe_res = MagicMock()
    mock_probe_res.stdout = "   \n" # Empty string means no stream found
    monkeypatch.setattr("core.player.ProcessRunner.run_blocking", MagicMock(return_value=mock_probe_res))

    res = player.play("/fake.m4b", 0, 10, 1.0, 100, False, False)

    assert res is False
    player.on_error.assert_called_once_with("NO_AUDIO")

# --- Process Monitoring & Hooks ---

def test_monitor_success_and_error(player):
    """Verifies the background thread triggers the correct UI hooks on exit."""
    mock_proc = MagicMock()
    player.process = mock_proc
    player.is_playing = True

    # 1. Simulate natural chapter end (Exit Code 0)
    mock_proc.returncode = 0
    player._monitor(mock_proc)
    player.on_complete.assert_called_once()

    # 2. Simulate FFplay crash (Exit Code 1)
    mock_proc.returncode = 1
    player._monitor(mock_proc)
    player.on_error.assert_called_once_with(1)

# --- Process Termination & Volume (OS Specific) ---

def test_stop_windows_taskkill(player, monkeypatch):
    """Verifies Windows uses the aggressive taskkill command."""
    monkeypatch.setattr(os, "name", "nt")
    mock_run_blocking = MagicMock()
    monkeypatch.setattr("core.player.ProcessRunner.run_blocking", mock_run_blocking)

    mock_proc = MagicMock()
    mock_proc.pid = 9999
    player.process = mock_proc

    player.stop()

    mock_run_blocking.assert_called_once()
    cmd = mock_run_blocking.call_args[0][0]
    assert "taskkill" in cmd
    assert "9999" in cmd
    assert player.process is None

def test_stop_unix_kill_and_fallback(player, monkeypatch):
    """Verifies Unix uses kill(), with a fallback to terminate() if it fails."""
    monkeypatch.setattr(os, "name", "posix")

    mock_proc = MagicMock()
    player.process = mock_proc

    # 1. Standard Kill
    player.stop()
    mock_proc.kill.assert_called_once()
    assert player.process is None

    # 2. Fallback Terminate (simulate kill() throwing an exception)
    player.process = mock_proc
    mock_proc.kill.side_effect = Exception("Kill denied")
    player.stop()
    mock_proc.terminate.assert_called_once()

def test_set_volume_windows_pycaw(player, monkeypatch):
    """Verifies Windows volume mapping via the external pycaw library."""
    monkeypatch.setattr(os, "name", "nt")

    # Build a deep mock structure to replicate pycaw's COM interfaces
    mock_volume_interface = MagicMock()
    mock_session = MagicMock()
    mock_session.Process.name.return_value = "ffplay.exe"
    mock_session._ctl.QueryInterface.return_value = mock_volume_interface

    mock_utilities = MagicMock()
    mock_utilities.GetAllSessions.return_value = [mock_session]

    mock_pycaw = MagicMock()
    mock_pycaw.AudioUtilities = mock_utilities
    mock_pycaw.ISimpleAudioVolume = "ISimpleAudioVolume"

    # Hijack the import mechanism so the player loads our mock instead of the real library
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", mock_pycaw)

    # 50% volume should translate to 0.5 float
    player.set_volume(50)

    mock_volume_interface.SetMasterVolume.assert_called_once_with(0.5, None)