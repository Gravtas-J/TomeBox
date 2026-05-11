import pytest
import time
from fastapi.testclient import TestClient
import os
from unittest.mock import MagicMock
# Adjust this import to match where web_app.py lives
from server.web_app import create_server_app
from tests.fakes import FakeTomebox

@pytest.fixture
def fake_tomebox():
    box = FakeTomebox()
    box.settings["auth_token"] = "test_master_token" 
    box._active_otps = {"123456": time.time() + 600}
    box.settings["profiles"] = ["Main", "KidsProfile", "WifeProfile"]
    
    box.library_manager.local_library = {
        "/fake/local.m4b": {"title": "Local Book", "format": "M4B", "asin": "LOC_123"}
    }
    box.library_manager.cloud_items = [
        {"title": "Cloud Book", "asin": "CLD_456", "authors": [{"name": "Cloud Author"}], "runtime_length_min": 600}
    ]
    return box

@pytest.fixture
def client(fake_tomebox):
    """Standard initialization to avoid 'app' shortcut warnings in some httpx versions."""
    app = create_server_app(fake_tomebox)
    return TestClient(app)

@pytest.fixture
def auth_client(client):
    client.cookies.set("tomebox_token", "test_master_token")
    return client


# --- Block 2: API Route Testing ---

def test_auth_pairing_valid_otp(client, fake_tomebox):
    """Verifies pairing using the modern follow_redirects parameter."""
    # We use follow_redirects=False here to catch the 302
    response = client.get("/auth?otp=123456", follow_redirects=False)
    
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert "tomebox_token" in response.cookies
    
    new_token = response.cookies["tomebox_token"]
    hashed_token = fake_tomebox.db.hash_device_token(new_token)
    assert hashed_token in fake_tomebox.settings["paired_devices"]

def test_auth_pairing_invalid_otp(client):
    """Verifies failed pairing."""
    response = client.get("/auth?otp=000000", follow_redirects=False)
    
    assert response.status_code == 401
    assert "Expired Link" in response.text

def test_api_profiles_reads_settings(auth_client):
    response = auth_client.get("/api/profiles")
    assert response.status_code == 200
    assert response.json() == ["Main", "KidsProfile", "WifeProfile"]

def test_api_library_shape_and_status(auth_client):
    response = auth_client.get("/api/library")
    assert response.status_code == 200
    library_dict = response.json()
    
    assert "/fake/local.m4b" in library_dict
    assert "cloud:CLD_456" in library_dict
    
    # Verify the Local Item was enriched correctly
    local_item = library_dict["/fake/local.m4b"]
    assert local_item["title"] == "Local Book"
    assert local_item["download_status"] == "downloaded"
    
    # Verify the Cloud Item was generated correctly
    cloud_item = library_dict["cloud:CLD_456"]
    assert cloud_item["title"] == "Cloud Book"
    assert cloud_item["download_status"] == "cloud_only"
    assert cloud_item["asin"] == "CLD_456"
    assert cloud_item["authors"] == "Cloud Author"

def test_api_import_security(auth_client, fake_tomebox, tmp_path):
    # Setup safe and evil temporary directories
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    evil_root = tmp_path / "evil"
    evil_root.mkdir()
    
    safe_file = safe_root / "book.m4b"
    safe_file.write_text("dummy audio")
    
    evil_file = evil_root / "passwd"
    evil_file.write_text("secrets")
    
    # Inject the safe root into the server's state
    fake_tomebox.import_root = str(safe_root)
    fake_tomebox.library_manager.import_files = MagicMock()
    
    # 1. Evil Path (Outside allowed root)
    res = auth_client.post("/api/library/import", json={"path": str(evil_file)})
    assert res.status_code == 403
    assert "Forbidden" in res.json().get("detail", "")
    
    # 2. Safe Path (Inside allowed root)
    res = auth_client.post("/api/library/import", json={"path": str(safe_file)})
    assert res.status_code == 200
    fake_tomebox.library_manager.import_files.assert_called_once()

def test_api_stream_range_parsing(auth_client, fake_tomebox, tmp_path):
    # Create a real temporary file to test byte streams
    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b"A" * 1024) # Exactly 1024 bytes
    path_str = str(audio_file)
    
    # Register the temp file in the fake library
    fake_tomebox.library_manager.local_library[path_str] = {"title": "Test Stream"}
    
    # 1. Standard Request (Full File)
    res = auth_client.get(f"/api/stream?path={path_str}")
    assert res.status_code == 200
    assert len(res.content) == 1024
    
    # 2. Valid Range (0-499 = exactly 500 bytes)
    res = auth_client.get(f"/api/stream?path={path_str}", headers={"Range": "bytes=0-499"})
    assert res.status_code == 206
    assert len(res.content) == 500
    assert res.headers["Content-Range"] == "bytes 0-499/1024"
    
    # 3. Beyond EOF Range
    res = auth_client.get(f"/api/stream?path={path_str}", headers={"Range": "bytes=2048-4096"})
    assert res.status_code == 416

def test_api_bookmarks_crud(auth_client, fake_tomebox):
    path = "/fake/local.m4b"
    
    # 1. Add Bookmark
    res = auth_client.post("/api/library/bookmarks", json={
        "path": path,
        "time": 120.5,
        "note": "Great quote"
    })
    assert res.status_code == 200
    
    entry = fake_tomebox.library_manager.local_library[path]
    assert len(entry["bookmarks"]) == 1
    assert entry["bookmarks"][0]["time"] == 120.5
    
    # 2. Read Bookmarks
    res = auth_client.get(f"/api/library/bookmarks?path={path}")
    assert res.status_code == 200
    assert len(res.json()["bookmarks"]) == 1
    
    # 3. Delete Bookmark (Using .request to safely pass a JSON body to a DELETE method)
    res = auth_client.request("DELETE", "/api/library/bookmarks", json={
        "path": path,
        "index": 0
    })
    assert res.status_code == 200
    assert len(entry["bookmarks"]) == 0

def test_api_progress_update(auth_client, fake_tomebox):
    path = "/fake/local.m4b"
    
    # Simulate a client passing a float wrapped as a string
    res = auth_client.post("/api/progress", json={
        "path": path,
        "position": "145.6", 
        "profile": "Main"
    })
    
    assert res.status_code == 200
    entry = fake_tomebox.library_manager.local_library[path]
    
    # Verify the backend correctly parsed it into a float and mapped the profile
    assert entry["progress"]["Main"] == 145.6
    assert entry["last_position"] == 145.6
    assert fake_tomebox.settings["last_played_Main"] == path