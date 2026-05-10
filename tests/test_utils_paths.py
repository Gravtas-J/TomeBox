import os
import sys
import pytest
from core.utils.paths import get_resource_path, parse_dnd_paths

# --- parse_dnd_paths ---

def test_parse_dnd_paths_with_braces_and_spaces(monkeypatch):
    monkeypatch.setattr(os.path, "exists", lambda path: True)
    
    # tkinterdnd2 format: {path with spaces} path_without_spaces {another one}
    raw_data = '{C:/My Audiobooks/Book 1.m4b} C:/Downloads/Book2.m4b {/unix/path/Book 3.mp3}'
    paths = parse_dnd_paths(raw_data)
    
    assert len(paths) == 3
    assert os.path.normpath("C:/My Audiobooks/Book 1.m4b") in paths
    assert os.path.normpath("C:/Downloads/Book2.m4b") in paths
    assert os.path.normpath("/unix/path/Book 3.mp3") in paths

def test_parse_dnd_paths_filters_nonexistent(monkeypatch):
    def mock_exists(path):
        return "exists.txt" in path
    
    monkeypatch.setattr(os.path, "exists", mock_exists)
    
    raw_data = '{C:/fake/path/missing.txt} C:/real/path/exists.txt'
    paths = parse_dnd_paths(raw_data)
    
    assert len(paths) == 1
    assert os.path.normpath("C:/real/path/exists.txt") in paths

def test_parse_dnd_paths_empty_or_malformed():
    assert parse_dnd_paths("") == []
    assert parse_dnd_paths("{} {}") == []

# --- get_resource_path ---

def test_get_resource_path_source_mode(monkeypatch):
    # Ensure sys.frozen is False
    if hasattr(sys, 'frozen'):
        monkeypatch.delattr(sys, 'frozen')
        
    result = get_resource_path("server", "static", "css")
    
    # In source mode, it walks up from core/utils/paths.py to the project root
    assert "server" in result
    assert "static" in result
    assert "css" in result

def test_get_resource_path_frozen_mode(monkeypatch):
    # Mock PyInstaller's runtime environment
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', '/tmp/pyinstaller_mock', raising=False)
    
    result = get_resource_path("server", "static", "css")
    
    assert result == os.path.join('/tmp/pyinstaller_mock', "server", "static", "css")