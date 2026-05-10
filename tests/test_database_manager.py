import os
import json
import sqlite3
import pytest
from core.database import DatabaseManager

@pytest.fixture
def db(tmp_path):
    """Provides a fresh DatabaseManager instance connected to a temporary directory."""
    return DatabaseManager(base_dir=str(tmp_path))

def test_initialization_mints_default_settings(db):
    settings = db.load_settings()
    
    # Verify the automatic minting of core security/identity keys
    assert "auth_token" in settings
    assert "device_salt" in settings
    assert "paired_devices" in settings
    assert isinstance(settings["paired_devices"], dict)

def test_settings_read_write_roundtrip(db):
    # Pre-load to ensure defaults are minted so we don't overwrite them accidentally
    settings = db.load_settings()
    
    settings["theme"] = "dark"
    settings["volume"] = 75
    settings["nested_dict"] = {"a": 1, "b": 2}
    
    db.save_settings(settings)
    
    loaded = db.load_settings()
    assert loaded["theme"] == "dark"
    assert loaded["volume"] == 75
    assert loaded["nested_dict"]["a"] == 1

def test_device_token_hashing(db):
    settings = db.load_settings()
    salt = settings["device_salt"]
    
    token = "test_token_123"
    hashed = db.hash_device_token(token)
    
    # Verify it matches a manual sha256 of salt+token
    import hashlib
    expected = hashlib.sha256(f"{salt}{token}".encode()).hexdigest()
    assert hashed == expected

def test_save_local_db_deletes_removed_paths(db, monkeypatch):
    # Mock os.path.exists so load_local_db doesn't strip our fake test paths
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    
    initial_library = {
        "/fake/path1.m4b": {"title": "Book 1", "format": "M4B"},
        "/fake/path2.m4b": {"title": "Book 2", "format": "M4B"}
    }
    
    db.save_local_db(initial_library)
    loaded = db.load_local_db()
    assert len(loaded) == 2
    
    # Remove path1, add path3
    updated_library = {
        "/fake/path2.m4b": {"title": "Book 2", "format": "M4B"},
        "/fake/path3.mp3": {"title": "Book 3", "format": "MP3"}
    }
    
    db.save_local_db(updated_library)
    loaded_again = db.load_local_db()
    
    assert len(loaded_again) == 2
    assert "/fake/path2.m4b" in loaded_again
    assert "/fake/path3.mp3" in loaded_again
    assert "/fake/path1.m4b" not in loaded_again  # Successfully deleted