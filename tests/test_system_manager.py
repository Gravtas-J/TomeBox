import os
import sys
import json
import pytest
import subprocess
from unittest.mock import MagicMock
from core.controllers.system_manager import SystemManager
from core.utils.process_runner import ProcessRunner

@pytest.fixture
def manager():
    """Provides a fresh SystemManager instance with a mocked logger."""
    mock_logger = MagicMock()
    return SystemManager(logger=mock_logger)

# --- 1. Pending Imports Logic (Lines 327-374) ---

def test_pending_imports_lifecycle(manager, tmp_path):
    """Verifies adding, loading, removing, and clearing the pending imports JSON."""
    data_dir = str(tmp_path)
    
    # 1. Add items
    manager.add_pending_import(data_dir, "/fake/folder", is_folder=True)
    manager.add_pending_import(data_dir, "/fake/file.m4b", is_folder=False)
    
    # Verify both exist
    loaded = manager.load_pending_imports(data_dir)
    assert len(loaded) == 2
    assert {"path": "/fake/folder", "is_folder": True} in loaded
    
    # 2. Prevent duplicates
    manager.add_pending_import(data_dir, "/fake/file.m4b", is_folder=False)
    assert len(manager.load_pending_imports(data_dir)) == 2
    
    # 3. Remove an item
    manager.remove_pending_import(data_dir, "/fake/file.m4b")
    loaded = manager.load_pending_imports(data_dir)
    assert len(loaded) == 1
    assert loaded[0]["path"] == "/fake/folder"
    
    # 4. Clear all
    manager.clear_all_pending_imports(data_dir)
    assert len(manager.load_pending_imports(data_dir)) == 0
    assert not os.path.exists(manager.get_pending_imports_file(data_dir))

# --- 2. File Cleanup Logic (Lines 114-173) ---

def test_cleanup_orphaned_files(manager, tmp_path):
    """Verifies the startup scan correctly targets empty and temporary files while leaving valid ones alone."""
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    
    # Create test files
    valid_file = dl_dir / "good_book.m4b"
    valid_file.write_text("audio_data") # Has size
    
    empty_file = dl_dir / "corrupt_book.aax"
    empty_file.touch() # 0 bytes
    
    part_file = dl_dir / "downloading.part"
    part_file.write_text("partial_data")
    
    temp_file = dl_dir / "processing.tmp.m4b"
    temp_file.write_text("ffmpeg_temp_data")
    
    # Execute cleanup
    manager.cleanup_orphaned_files(str(dl_dir))
    
    # Verify results
    assert valid_file.exists()
    assert not empty_file.exists()
    assert not part_file.exists()
    assert not temp_file.exists()

# --- 3. Disk Space Logic (Lines 310-320) ---

def test_has_enough_disk_space(manager, monkeypatch):
    """Tests the disk space verifier against mock shutil limits."""
    # Mock shutil.disk_usage to return: (total, used, free)
    monkeypatch.setattr("shutil.disk_usage", lambda d: (1000, 500, 500))
    # Short-circuit the loop that climbs the directory tree looking for a valid path
    monkeypatch.setattr("os.path.exists", lambda d: True)
    
    # Requires 400, has 500 -> True
    assert manager.has_enough_disk_space("/fake/dir", 400) is True
    # Requires 600, has 500 -> False
    assert manager.has_enough_disk_space("/fake/dir", 600) is False

def test_has_enough_disk_space_fallback(manager, monkeypatch):
    """Verifies it gracefully fails open (returns True) if the OS rejects the query."""
    def mock_usage(d): raise Exception("Permission Denied")
    monkeypatch.setattr("shutil.disk_usage", mock_usage)
    monkeypatch.setattr("os.path.exists", lambda d: True)
    
    assert manager.has_enough_disk_space("/fake/dir", 9999999) is True

# --- 4. Network Utilities (Lines 176-184) ---

def test_get_local_ip_success(manager, monkeypatch):
    mock_socket = MagicMock()
    mock_socket.return_value.getsockname.return_value = ["192.168.1.50"]
    monkeypatch.setattr("socket.socket", mock_socket)
    
    assert manager.get_local_ip() == "192.168.1.50"

def test_get_local_ip_fallback(manager, monkeypatch):
    def mock_connect(*args):
        raise Exception("Network Unreachable")
    
    mock_socket = MagicMock()
    mock_socket.return_value.connect = mock_connect
    monkeypatch.setattr("socket.socket", mock_socket)
    
    assert manager.get_local_ip() == "127.0.0.1"

# --- 5. Windows OS Interfacing (Lines 90-110, 187-228) ---

def test_toggle_system_sleep(manager, monkeypatch):
    """Verifies the Windows sleep prevention API is invoked correctly."""
    monkeypatch.setattr("os.name", "nt")
    
    mock_ctypes = MagicMock()
    monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)
    
    # Call with True
    manager.toggle_system_sleep(prevent_sleep=True)
    
    # 0x80000000 | 0x00000001 | 0x00000002 = 2147483651 (Continuous | System | Display)
    mock_ctypes.windll.kernel32.SetThreadExecutionState.assert_called_with(2147483651)
    
    # Call with False
    manager.toggle_system_sleep(prevent_sleep=False)
    
    # 0x80000000 = 2147483648 (Continuous)
    mock_ctypes.windll.kernel32.SetThreadExecutionState.assert_called_with(2147483648)

def test_firewall_rule_checks(manager, monkeypatch):
    """Verifies the firewall checker logic without querying the real host OS."""
    
    # 1. Create a fake subprocess result that pretends the rule exists
    mock_result_exists = MagicMock()
    mock_result_exists.returncode = 0
    mock_result_exists.stdout = "Rule Name: TomeBox" # Or whatever your checker looks for
    
    # 2. Intercept the system call (Adjust the import path to match whatever 
    monkeypatch.setattr("core.utils.process_runner.ProcessRunner.run_blocking", MagicMock(return_value=mock_result_exists))
    
    # 3. Assert the True path
    assert manager._is_firewall_rule_installed() is True
    
    # 4. Assert the False path
    mock_result_missing = MagicMock()
    mock_result_missing.returncode = 1
    mock_result_missing.stdout = "No rules match the specified criteria."
    
    monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_result_missing))
    assert manager._is_firewall_rule_installed() is False

def test_add_firewall_rule(manager, monkeypatch):
    """Verifies UAC escalation call for adding firewall rules."""
    mock_ctypes = MagicMock()
    # ShellExecuteW returning > 32 indicates success in Windows API
    mock_ctypes.windll.shell32.ShellExecuteW.return_value = 42 
    monkeypatch.setitem(sys.modules, "ctypes", mock_ctypes)
    
    assert manager._add_firewall_rule(port=8000) is True
    mock_ctypes.windll.shell32.ShellExecuteW.assert_called_once()
    
    # Test User Denial
    mock_ctypes.windll.shell32.ShellExecuteW.return_value = 5 # Access Denied code
    assert manager._add_firewall_rule(port=8000) is False