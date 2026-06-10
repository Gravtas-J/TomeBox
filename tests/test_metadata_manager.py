import os
from unittest.mock import MagicMock

import pytest
import requests

from core.controllers.metadata_manager import MetadataManager

# --- Mock Infrastructure ---


@pytest.fixture
def sync_pool():
    """Forces threaded tasks to execute instantly on the main thread."""

    class Pool:
        def submit(self, fn, *args, **kwargs):
            # Strip out pool-specific kwargs so they don't crash the parameterless workers
            kwargs.pop("task_type", None)
            fn(*args, **kwargs)

    return Pool()


@pytest.fixture
def manager(sync_pool):
    """Provides a MetadataManager with mocked API and Library dependencies."""
    mock_api = MagicMock()
    mock_api.auth = True

    mock_lib_mgr = MagicMock()
    mock_lib_mgr.local_library = {
        "/fake/audiobook.m4b": {"title": "Old Title", "asin": "OLD_123"}
    }

    callbacks = {
        "on_search_complete": MagicMock(),
        "on_apply_complete": MagicMock(),
        "on_display_ready": MagicMock(),
        "on_error": MagicMock(),
    }

    return MetadataManager(
        api_client=mock_api,
        library_manager=mock_lib_mgr,
        logger=MagicMock(),
        covers_dir="/fake/covers",
        callbacks=callbacks,
        thread_pool=sync_pool,
        start_workers=True,
    )


# --- The Fallback Chain Tests ---


def test_search_audible_success(manager, monkeypatch):
    """Test 1: If Audible succeeds, it completely ignores Google Books and Local Tags."""
    manager.api.search_catalog.return_value = [
        {"asin": "AUD_1", "title": "Audible Match"}
    ]
    mock_gb = MagicMock()
    monkeypatch.setattr(manager, "search_google_books", mock_gb)

    manager.event_bus.publish = MagicMock()  # Mock the bus
    manager.search_catalog("/fake/audiobook.m4b", "Test Query")

    # Verify the event bus published the Audible result
    manager.event_bus.publish.assert_any_call(
        "metadata.search_complete",
        filepath="/fake/audiobook.m4b",
        products=[{"asin": "AUD_1", "title": "Audible Match", "source": "Audible"}],
    )


def test_search_fallback_to_google_books(manager, monkeypatch):
    """Test 2: If Audible fails (e.g., 404 or rate limit), it falls back to Google Books."""
    manager.api.search_catalog.side_effect = Exception("404 Product Not Found")
    mock_gb = MagicMock(
        return_value=[{"asin": "GB_1", "title": "GB Match", "source": "Google"}]
    )
    monkeypatch.setattr(manager, "search_google_books", mock_gb)

    manager.event_bus.publish = MagicMock()  # Mock the bus
    manager.search_catalog("/fake/audiobook.m4b", "Test Query")

    mock_gb.assert_called_once_with("Test Query")

    # Verify the event bus published the GB result
    manager.event_bus.publish.assert_any_call(
        "metadata.search_complete",
        filepath="/fake/audiobook.m4b",
        products=[{"asin": "GB_1", "title": "GB Match", "source": "Google"}],
    )


def test_search_fallback_to_local_tags(manager, monkeypatch):
    """Test 3: If both APIs fail or return nothing, it rips data from the local file tags."""
    manager.api.search_catalog.side_effect = Exception("Network Error")
    monkeypatch.setattr(manager, "search_google_books", MagicMock(return_value=[]))

    mock_converter = MagicMock()
    mock_converter.return_value.get_metadata_and_chapters.return_value = {
        "format": {"tags": {"title": "Embedded Title", "artist": "Embedded Author"}}
    }
    monkeypatch.setattr(
        "core.controllers.metadata_manager.AudioConverter", mock_converter
    )

    manager.event_bus.publish = MagicMock()  # Mock the bus
    manager.search_catalog("/fake/audiobook.m4b", "Test Query")

    # Extract the results from the event bus call
    calls = [
        c
        for c in manager.event_bus.publish.call_args_list
        if c[0][0] == "metadata.search_complete"
    ]
    results = calls[0].kwargs["products"]

    assert len(results) == 1
    assert results[0]["title"] == "Embedded Title"


# --- Scraping & Apply Logic ---


def test_apply_scraped_metadata_google_books(manager, monkeypatch):
    """Verifies that API data is correctly parsed, saved to the DB, and passed to FFmpeg."""
    filepath = "/fake/audiobook.m4b"
    selected_asin = "GB_999"
    cover_path = os.path.join("/fake/covers", f"{selected_asin}.jpg")
    manager.event_bus.publish = MagicMock()
    manager.apply_scraped_metadata(filepath, selected_asin)

    # Mock requests.get to return a fake Google Books volume AND fake image bytes
    class MockResponse:
        def __init__(self, json_data, content, status_code=200):
            self._json_data = json_data
            self.content = content
            self.status_code = status_code

        def json(self):
            return self._json_data

    def mock_requests_get(url, *args, **kwargs):
        if "googleapis.com" in url:
            return MockResponse(
                {
                    "volumeInfo": {
                        "title": "New GB Title",
                        "authors": ["New GB Author"],
                        "imageLinks": {"thumbnail": "https://fake.url/cover.jpg"},
                    }
                },
                b"",
            )
        elif "cover.jpg" in url:
            return MockResponse({}, b"fake_image_bytes")
        return MockResponse({}, b"", 404)

    monkeypatch.setattr("requests.get", mock_requests_get)

    # Mock filesystem operations to pretend the cover saved successfully
    mock_open = MagicMock()
    monkeypatch.setattr("builtins.open", mock_open)
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    monkeypatch.setattr(os, "replace", MagicMock())

    # Mock FFmpeg execution
    mock_run = MagicMock()
    mock_run.return_value.returncode = 0
    monkeypatch.setattr(
        "core.controllers.metadata_manager.ProcessRunner.run_blocking", mock_run
    )

    # Execute the method
    manager.apply_scraped_metadata(filepath, selected_asin)

    # 1. Verify the DB was updated with the parsed data
    updated_db_entry = manager.library_manager.local_library[filepath]
    assert updated_db_entry["title"] == "New GB Title"
    assert updated_db_entry["authors"] == "New GB Author"
    assert updated_db_entry["asin"] == selected_asin

    # 2. Verify the image data was fetched and written to disk
    mock_open.assert_called_with(cover_path, "wb")

    # 3. Verify FFmpeg was triggered with the correct embed arguments
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]

    assert "ffmpeg" in cmd
    assert filepath in cmd
    assert cover_path in cmd
    assert "title=New GB Title" in cmd
    assert "artist=New GB Author" in cmd

    # 4. Verify the completion callback fired
    manager.event_bus.publish.assert_any_call(
        "metadata.apply_complete",
        filepath=filepath,
        title="New GB Title",
        is_manual=False,
    )


def test_extract_embedded_cover(manager, monkeypatch):
    """Verifies FFmpeg is correctly called to extract embedded cover art."""
    mock_run = MagicMock()
    mock_run.return_value.returncode = 0
    monkeypatch.setattr(
        "core.controllers.metadata_manager.ProcessRunner.run_blocking", mock_run
    )
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    monkeypatch.setattr(os.path, "getsize", lambda p: 1024)

    result = manager.extract_embedded_cover("/fake/in.mp3", "/fake/out.jpg")

    assert result is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "-vcodec" in cmd
    assert "copy" in cmd


def test_search_google_books_api_responses(manager, monkeypatch):
    """Tests the Google Books scraper against 200 OK, 429 Rate Limit, and Timeouts."""

    class MockResp:
        def __init__(self, status, json_data=None):
            self.status_code = status
            self._json = json_data or {}

        def json(self):
            return self._json

    # 1. Success case
    mock_get = MagicMock(
        return_value=MockResp(
            200,
            {
                "items": [
                    {
                        "id": "123",
                        "volumeInfo": {"title": "GB Title", "authors": ["GB Author"]},
                    }
                ]
            },
        )
    )
    monkeypatch.setattr("requests.get", mock_get)

    res = manager.search_google_books("query")
    assert len(res) == 1
    assert res[0]["title"] == "GB Title"
    assert res[0]["asin"] == "GB_123"

    # 2. Rate Limit case (Ensure it doesn't crash)
    mock_get.return_value = MockResp(429)
    res_429 = manager.search_google_books("query")
    assert len(res_429) == 0

    # 3. Timeout case (Ensure it catches the specific requests exception)
    mock_get.side_effect = requests.exceptions.Timeout("Timed out")
    res_timeout = manager.search_google_books("query")
    assert len(res_timeout) == 0


# --- Audible Injection & Legacy Apply Logic (Lines 188-222, 232-279) ---


def test_apply_scraped_metadata_audible_route(manager, monkeypatch):
    """Verifies Audible API metadata is mapped correctly and pushed to FFmpeg."""
    manager.api.auth = True
    filepath = "/fake/book.m4b"
    manager.library_manager.local_library = {filepath: {"asin": "OLD_ASIN"}}

    # Mock Audible API response
    mock_client = MagicMock()
    mock_client.return_value.get.return_value = {
        "product": {
            "title": "Audible Title",
            "authors": [{"name": "Audible Author"}],
            "series": [{"title": "Series Name", "sequence": "1"}],
            "runtime_length_min": 120,
            "product_images": {"500": "https://fake.img"},
        }
    }
    monkeypatch.setattr("core.controllers.metadata_manager.audible.Client", mock_client)

    # Mock image downloading
    mock_get = MagicMock()
    mock_get.return_value.content = b"fake_image_data"
    monkeypatch.setattr("requests.get", mock_get)
    mock_open = MagicMock()
    monkeypatch.setattr("builtins.open", mock_open)

    # Mock FFmpeg execution
    mock_run = MagicMock()
    mock_run.return_value.returncode = 0
    monkeypatch.setattr(
        "core.controllers.metadata_manager.ProcessRunner.run_blocking", mock_run
    )

    # Mock OS to pretend the new cover exists, and the old cover gets deleted
    def mock_exists(path):
        return "AUD_123" in str(path) or "OLD_ASIN" in str(path)

    monkeypatch.setattr(os.path, "exists", mock_exists)
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    mock_remove = MagicMock()
    monkeypatch.setattr(os, "remove", mock_remove)
    monkeypatch.setattr(os, "replace", MagicMock())

    manager.apply_scraped_metadata(filepath, "AUD_123")

    # 1. Verify DB update
    updated = manager.library_manager.local_library[filepath]
    assert updated["title"] == "Audible Title"
    assert updated["authors"] == "Audible Author"
    assert "Series Name" in updated["series"]
    assert updated["duration_min"] == 120

    # 2. Verify Cover download and cleanup of the old cover
    mock_get.assert_called_with("https://fake.img", timeout=10)
    mock_open.assert_called_with(os.path.join(manager.covers_dir, "AUD_123.jpg"), "wb")
    assert mock_remove.call_count >= 1  # Cleanup was triggered

    # 3. Verify FFmpeg embed
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert any("Audible Title" in item for item in cmd)
    assert any("-disposition:v" in item for item in cmd)


# --- Display Fetch Cascade (Lines 286-417) ---


def test_fetch_from_google_books(manager, monkeypatch):
    """Directly tests the standalone Google Books metadata fetcher."""

    class MockResp:
        status_code = 200

        def json(self):
            return {
                "items": [
                    {
                        "volumeInfo": {
                            "authors": ["Direct Author"],
                            "imageLinks": {"thumbnail": "http://img.jpg"},
                        }
                    }
                ]
            }

    monkeypatch.setattr("requests.get", MagicMock(return_value=MockResp()))

    authors, url = manager.fetch_from_google_books("query")
    assert authors == "Direct Author"
    assert url == "https://img.jpg"  # Verifies the http -> https redirect fix


def test_fetch_display_metadata_cascade(manager, monkeypatch):
    """Forces the system to fallback through the cloud, local rips, and Google Books."""
    filepath = "/fake/book.m4b"
    manager.library_manager.local_library = {filepath: {"title": "Test Book"}}
    manager.library_manager.cloud_items = []
    manager.api.auth = False

    # Force embedded cover extraction to fail
    monkeypatch.setattr(
        manager, "extract_embedded_cover", MagicMock(return_value=False)
    )
    monkeypatch.setattr(os.path, "exists", lambda p: False)

    # Mock the final Google Books fallback
    mock_gb = MagicMock(return_value=("GB Author", "https://gb.cover"))
    monkeypatch.setattr(manager, "fetch_from_google_books", mock_gb)

    # Mock the image download
    mock_get = MagicMock()
    mock_get.return_value.content = b"gb_image_data"
    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("builtins.open", MagicMock())

    manager.fetch_display_metadata(filepath)

    # Check that UI was updated with the Google Books data
    manager.event_bus.publish = MagicMock()
    manager.fetch_display_metadata(filepath)

    # Check that UI was updated via the event bus
    assert any(
        call[0][0] == "metadata.display_ready"
        for call in manager.event_bus.publish.call_args_list
    )


# --- Background Missing Cover Sync (Lines 421-452) ---


def test_sync_missing_covers(manager, monkeypatch):
    """Verifies it iterates over cloud items and downloads only the missing images."""
    manager.library_manager.cloud_items = [
        {"asin": "111", "product_images": {"500": "https://img1"}},
        {"asin": "222", "product_images": {"252": "https://img2"}},
        {"asin": "333"},  # Item with no image data
    ]

    # Pretend cover 111 already exists, but 222 does not
    monkeypatch.setattr(os.path, "exists", lambda p: "111" in str(p))

    mock_get = MagicMock()
    mock_get.return_value.content = b"image_data"
    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("builtins.open", MagicMock())

    mock_complete = MagicMock()
    manager.sync_missing_covers(on_complete_cb=mock_complete)

    # It should have skipped 111, downloaded 222, and skipped 333
    mock_get.assert_called_once_with("https://img2", timeout=10)
    mock_complete.assert_called_once()


def test_apply_manual_metadata_custom_cover_and_embed(manager, monkeypatch):
    """Tests manual metadata application with a custom cover image and FFmpeg embedding."""
    filepath = "/fake/book.m4b"
    manager.library_manager.local_library = {
        filepath: {"title": "Old Title", "asin": "OLD_123"}
    }

    new_data = {
        "title": "Manual Title",
        "authors": "Manual Author",
        "series": "Manual Series",
        "asin": "NEW_456",
    }

    # Mock OS existence so it triggers the custom cover flow
    monkeypatch.setattr("os.path.exists", lambda p: True)
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    # --- FIXED MOCKING LOGIC ---
    mock_img_instance = MagicMock()
    mock_img_instance.mode = "RGBA"  # Simulate a PNG with transparency
    mock_img_instance.convert.return_value = mock_img_instance

    # Patch PIL.Image.open directly at the source
    mock_open = MagicMock(return_value=mock_img_instance)
    monkeypatch.setattr("PIL.Image.open", mock_open)
    # ---------------------------

    # Mock FFmpeg execution and file replacement
    mock_run = MagicMock()
    mock_run.return_value.returncode = 0
    monkeypatch.setattr(
        "core.controllers.metadata_manager.ProcessRunner.run_blocking", mock_run
    )
    monkeypatch.setattr("os.replace", MagicMock())
    manager.event_bus.publish = MagicMock()

    # Execute
    manager.apply_manual_metadata(
        filepath, new_data, embed_to_file=True, new_cover_path="/fake/custom_upload.png"
    )

    # 1. Verify DB Updates
    updated = manager.library_manager.local_library[filepath]
    assert updated["title"] == "Manual Title"
    assert updated["authors"] == "Manual Author"
    assert updated["series"] == "Manual Series"
    assert updated["asin"] == "NEW_456"

    # 2. Verify PIL Image conversion (RGBA -> RGB for JPEG requirement)
    mock_open.assert_called_with("/fake/custom_upload.png")
    mock_img_instance.convert.assert_called_with("RGB")
    mock_img_instance.save.assert_called_once()

    # 3. Verify FFmpeg Embedding flags
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "title=Manual Title" in cmd
    assert "artist=Manual Author" in cmd
    assert "show=Manual Series" in cmd
    assert "series=Manual Series" in cmd

    # 4. Verify EventBus Notification fired for the UI
    manager.event_bus.publish.assert_any_call(
        "metadata.apply_complete",
        filepath=filepath,
        title="Manual Title",
        is_manual=True,
    )
