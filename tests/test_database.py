import hashlib
import os

import pytest

from core.database import DatabaseManager

# --- Setup & Fixtures ---


@pytest.fixture
def temp_db(tmp_path):
    """Provides an isolated DatabaseManager instance in a temporary directory."""
    return DatabaseManager(str(tmp_path))


# --- Initialization & Utility Paths ---


def test_initialization(temp_db, tmp_path):
    """Verifies tables are created and WAL mode is set."""
    assert os.path.exists(temp_db.db_path)

    # Verify tables
    cursor = temp_db.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    assert "settings" in tables
    assert "library" in tables


def test_utility_paths(temp_db):
    """Verifies JSON path generators for auth and cloud cache."""
    auth_path = temp_db.get_auth_path("Main")
    assert auth_path.endswith("auth_Main.json")
    assert "data" in auth_path

    cloud_path = temp_db.get_cloud_cache_path("Kids")
    assert cloud_path.endswith("cloud_Kids.json")
    assert "data" in cloud_path


# --- Settings Table Tests ---


def test_settings_lifecycle_and_defaults(temp_db):
    """Verifies default generation, saving, and loading of settings."""
    # 1. Load fresh (should generate auth_token, device_salt, and paired_devices)
    settings = temp_db.load_settings()
    assert "auth_token" in settings
    assert "device_salt" in settings
    assert settings["paired_devices"] == {}

    # 2. Modify and Save
    settings["active_profile"] = "WifeProfile"
    temp_db.save_settings(settings)

    # 3. Reload and Verify
    reloaded = temp_db.load_settings()
    assert reloaded["active_profile"] == "WifeProfile"
    # Ensure it didn't regenerate the tokens
    assert reloaded["auth_token"] == settings["auth_token"]


def test_settings_json_fallback(temp_db):
    """Verifies it gracefully handles plain text or corrupted JSON in the settings table."""
    cursor = temp_db.conn.cursor()
    # Insert raw, non-JSON text
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("bad_json_key", "raw_string_value"),
    )
    temp_db.conn.commit()

    settings = temp_db.load_settings()
    # Should fall back to the raw string in the exception block
    assert settings["bad_json_key"] == "raw_string_value"


def test_hash_device_token(temp_db):
    """Verifies the SHA256 hashing uses the DB's master salt."""
    settings = temp_db.load_settings()
    salt = settings["device_salt"]

    raw_token = "my_secret_device_id"
    expected_hash = hashlib.sha256(f"{salt}{raw_token}".encode()).hexdigest()

    assert temp_db.hash_device_token(raw_token) == expected_hash


def test_hash_device_token_json_fallback(temp_db):
    """Verifies hash_device_token handles a corrupted/plain text salt gracefully."""
    cursor = temp_db.conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("device_salt", "raw_salt"),
    )
    temp_db.conn.commit()

    # Should fall back to the raw string in the exception block
    expected_hash = hashlib.sha256(b"raw_salt_token").hexdigest()
    assert temp_db.hash_device_token("_token") == expected_hash


# --- Library Table Tests ---


def test_local_db_lifecycle(temp_db, monkeypatch):
    """Verifies insertion, updating, and deletion in the local library table."""
    # Mock os.path.exists to True so load_local_db doesn't cull our fake files
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    # 1. Save new item
    library = {"/fake/book.m4b": {"title": "Test Book", "duration_min": 60}}
    temp_db.save_local_db(library)
    assert temp_db.last_db_mtime > 0

    # 2. Load and verify
    loaded = temp_db.load_local_db()
    assert "/fake/book.m4b" in loaded
    assert loaded["/fake/book.m4b"]["title"] == "Test Book"

    # 3. Update existing item and remove an item
    library["/fake/book2.m4b"] = {"title": "Book 2"}
    del library["/fake/book.m4b"]
    temp_db.save_local_db(library)

    # 4. Load and verify deletion
    loaded_after = temp_db.load_local_db()
    assert "/fake/book.m4b" not in loaded_after
    assert "/fake/book2.m4b" in loaded_after


def test_local_db_missing_file_culling(temp_db, monkeypatch):
    """Verifies that load_local_db drops entries if the actual file no longer exists on disk."""
    # Pretend it exists to save it
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    temp_db.save_local_db({"/fake/deleted.m4b": {"title": "Deleted"}})

    # Pretend it was deleted before load
    monkeypatch.setattr(os.path, "exists", lambda p: "tomebox.db" in str(p))
    loaded = temp_db.load_local_db()

    assert "/fake/deleted.m4b" not in loaded


def test_local_db_malformed_json_skip(temp_db, monkeypatch):
    """Verifies that corrupt JSON in a library row doesn't crash the entire load process."""
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    cursor = temp_db.conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO library (path, data) VALUES (?, ?)",
        ("/fake/corrupt.m4b", "{bad_json"),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO library (path, data) VALUES (?, ?)",
        ("/fake/good.m4b", '{"title": "Good"}'),
    )
    temp_db.conn.commit()

    loaded = temp_db.load_local_db()

    # Should skip the corrupt one but successfully load the good one
    assert "/fake/corrupt.m4b" not in loaded
    assert "/fake/good.m4b" in loaded


def test_local_db_missing_database_file(temp_db, monkeypatch):
    """Verifies it safely returns an empty dict if the .db file itself vanishes."""
    # Force the initial os.path.exists check in load_local_db to fail
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    assert temp_db.load_local_db() == {}
