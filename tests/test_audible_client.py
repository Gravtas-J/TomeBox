import os
from unittest.mock import MagicMock

import pytest

from api.audible_client import (
    APIUnavailableError,
    AudibleClient,
    RateLimitError,
    find_key_iv_in_voucher,
    find_url_in_response,
)

# --- Pure Function Tests (Lines 20-49) ---


def test_find_url_in_response():
    # Test nested dict
    data_dict = {"layer1": {"layer2": {"offline_url": "https://test.url"}}}
    assert find_url_in_response(data_dict) == "https://test.url"

    # Test nested list
    data_list = [{"wrong": "data"}, {"layer1": {"offline_url": "https://list.url"}}]
    assert find_url_in_response(data_list) == "https://list.url"

    # Test missing
    assert find_url_in_response({"no": "url here"}) is None


def test_find_key_iv_in_voucher():
    # Test nested dict
    voucher_dict = {"data": {"license": {"key": "123", "iv": "abc"}}}
    assert find_key_iv_in_voucher(voucher_dict) == ("123", "abc")

    # Test missing
    assert find_key_iv_in_voucher({"key": "123"}) == (None, None)


# --- AudibleClient Core Tests ---


@pytest.fixture
def client():
    instance = object.__new__(AudibleClient)
    instance.auth = MagicMock()
    instance.logger = MagicMock()
    return instance


def test_auth_file_operations(client, monkeypatch):
    mock_exists = MagicMock(return_value=True)
    monkeypatch.setattr(os.path, "exists", mock_exists)

    # Mock the audible package's Authenticator
    mock_authenticator = MagicMock()
    monkeypatch.setattr(
        "api.audible_client.audible.Authenticator.from_file", mock_authenticator
    )

    # Test Load
    assert client.load_auth_from_file("/fake/auth.json") is True
    mock_authenticator.assert_called_once_with("/fake/auth.json")

    # Test Save
    client.save_auth_to_file("/fake/save.json")
    client.auth.to_file.assert_called_once_with("/fake/save.json")


def test_get_activation_bytes(client, monkeypatch):
    mock_sleep = MagicMock()
    monkeypatch.setattr("api.audible_client.time.sleep", mock_sleep)

    # 1. Success case
    client.auth.get_activation_bytes.return_value = "deadbeef"
    assert client.get_activation_bytes() == "deadbeef"

    # 2. Retry trap case (audible server delay bug)
    client.auth.get_activation_bytes.side_effect = [
        ValueError("data wrong length"),
        "recovered_bytes",
    ]
    assert client.get_activation_bytes() == "recovered_bytes"
    assert mock_sleep.call_count == 1

    # 3. Complete failure case
    client.auth.get_activation_bytes.side_effect = ValueError("data wrong length")
    assert client.get_activation_bytes() == ""


def test_fetch_library_pagination(client, monkeypatch):
    # Mock the internal backoff requester to simulate API pagination
    # Page 1 returns 1000 items, Page 2 returns 5 items
    mock_request = MagicMock()
    mock_request.side_effect = [
        {"items": [{"title": f"Book {i}"} for i in range(1000)]},
        {"items": [{"title": f"Book {i}"} for i in range(1000, 1005)]},
    ]
    monkeypatch.setattr(client, "_request_with_backoff", mock_request)

    # Need to mock audible.Client initialization inside the method
    monkeypatch.setattr("api.audible_client.audible.Client", MagicMock())

    items = client.fetch_library()

    assert len(items) == 1005
    assert mock_request.call_count == 2


def test_get_download_license_offline_key(client, monkeypatch):
    # Mock audible.Client and its post method
    mock_post = MagicMock()
    mock_post.return_value = {
        "offline_url": "https://download.audible",
        "content_license": {
            "content_metadata": {"content_key": {"offline_key": "fake_base64_key"}}
        },
    }
    mock_audible_client = MagicMock()
    mock_audible_client.return_value.post = mock_post
    monkeypatch.setattr("api.audible_client.audible.Client", mock_audible_client)

    # Mock RSA decryption to bypass actual cryptography
    mock_rsa_key = MagicMock()
    monkeypatch.setattr(
        "rsa.PrivateKey.load_pkcs1", MagicMock(return_value=mock_rsa_key)
    )
    monkeypatch.setattr(
        "rsa.decrypt", MagicMock(return_value=b"1234567890123456abcdefghijklmnop")
    )
    monkeypatch.setattr("base64.b64decode", MagicMock())

    client.auth.rsa_private_key = "fake_pem_string"

    url, a_key, a_iv = client.get_download_license("B00123")

    assert url == "https://download.audible"
    assert a_key == b"1234567890123456".hex()
    assert a_iv == b"abcdefghijklmnop".hex()


def test_find_key_iv_misses():
    """Ensures the recursive voucher parser gracefully returns (None, None) on bad data."""
    # Dict miss
    assert find_key_iv_in_voucher({"wrong": "data", "nested": {"still": "wrong"}}) == (
        None,
        None,
    )
    # List miss
    assert find_key_iv_in_voucher([{"wrong": "data"}]) == (None, None)


# --- Unauthenticated Traps & Auth Methods (Lines 77, 89, 95, 126-127, 173-175, 187, 203) ---


def test_unauthenticated_traps(client):
    """Verifies all major API methods safely bounce unauthenticated users."""
    client.auth = None

    assert client.is_authenticated() is False
    assert client.get_activation_bytes() == ""

    with pytest.raises(Exception, match="Not authenticated"):
        client.fetch_library()
    with pytest.raises(Exception, match="Not authenticated"):
        client.fetch_product_metadata("B00123")
    with pytest.raises(Exception, match="Not authenticated"):
        client.search_catalog("query")
    with pytest.raises(Exception, match="Not authenticated"):
        client.get_download_license("B00123")


def test_login_with_browser(client, monkeypatch):
    """Verifies the browser login callback is wired to the Audible package correctly."""
    mock_auth = MagicMock()
    monkeypatch.setattr(
        "api.audible_client.audible.Authenticator.from_login_external",
        MagicMock(return_value=mock_auth),
    )

    # Simulate a successful login callback
    result = client.login_with_browser("us", lambda x: None)

    assert result is True
    assert client.auth == mock_auth


# --- Standard API Methods (Lines 173-198) ---


def test_fetch_product_metadata_and_search(client, monkeypatch):
    """Verifies metadata and search methods extract the correct keys from the response."""
    client.auth = True

    # Mock the backoff requester
    mock_request = MagicMock()
    # Mock return for fetch_product_metadata
    mock_request.return_value = {
        "product": {"title": "Single Book"},
        "products": [{"title": "Search Book"}],
    }
    monkeypatch.setattr(client, "_request_with_backoff", mock_request)
    monkeypatch.setattr("api.audible_client.audible.Client", MagicMock())

    assert client.fetch_product_metadata("123").get("title") == "Single Book"
    assert client.search_catalog("query")[0].get("title") == "Search Book"


# --- DRM & License Logic (Lines 210, 227-252) ---


def test_get_download_license_no_url(client, monkeypatch):
    """Verifies it crashes cleanly if the API doesn't return a download link."""
    client.auth = True
    mock_client = MagicMock()
    # Return garbage data
    mock_client.return_value.post.return_value = {"random": "data"}
    monkeypatch.setattr("api.audible_client.audible.Client", mock_client)

    with pytest.raises(Exception, match="Could not find the offline download URL"):
        client.get_download_license("123")


def test_get_download_license_voucher_fallback(client, monkeypatch):
    """Verifies it falls back to voucher decryption if the offline_key is missing."""
    client.auth = True
    mock_client = MagicMock()
    mock_client.return_value.post.return_value = {
        "offline_url": "http://test",
        "content_license": {},
    }
    monkeypatch.setattr("api.audible_client.audible.Client", mock_client)

    # Mock the voucher decryption tool
    mock_decrypt = MagicMock(return_value={"key": "v_key", "iv": "v_iv"})
    monkeypatch.setattr(
        "api.audible_client.decrypt_voucher_from_licenserequest", mock_decrypt
    )

    url, key, iv = client.get_download_license("123")
    assert url == "http://test"
    assert key == "v_key"
    assert iv == "v_iv"


def test_get_drm_flags(client, monkeypatch):
    """Exhaustively tests the dynamic DRM flag generator for M4B conversion."""
    data_dir = "/fake/data"

    # Case 1: File already has its own specific key and IV embedded in the library
    flags = client.get_drm_flags(
        "book.aaxc",
        {"audible_key": "k1", "audible_iv": "i1"},
        "Main",
        "deadbeef",
        data_dir,
    )
    assert flags == ["-audible_key", "k1", "-audible_iv", "i1"]

    # Case 2: File belongs to the active profile, use the globally provided auth_bytes
    flags = client.get_drm_flags(
        "book.aax", {"owner": "Main"}, "Main", "deadbeef", data_dir
    )
    assert flags == ["-activation_bytes", "deadbeef"]

    # Case 3: File belongs to a DIFFERENT profile. Need to dynamically load their auth file.
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    mock_temp_auth = MagicMock()
    mock_temp_auth.get_activation_bytes.return_value = "dynamic_bytes"
    monkeypatch.setattr(
        "api.audible_client.audible.Authenticator.from_file",
        MagicMock(return_value=mock_temp_auth),
    )

    flags = client.get_drm_flags(
        "book.aax", {"owner": "Wife"}, "Main", "deadbeef", data_dir
    )
    assert flags == ["-activation_bytes", "dynamic_bytes"]

    # Case 4: File belongs to a different profile, but loading their auth fails. Fall back to current bytes.
    mock_temp_auth.get_activation_bytes.side_effect = Exception("Corrupt auth file")

    # Create a specific mock logger for this function
    mock_logger = MagicMock()

    # Pass the mock_logger explicitly into the function
    flags = client.get_drm_flags(
        "book.aax", {"owner": "Wife"}, "Main", "deadbeef", data_dir, logger=mock_logger
    )

    assert flags == ["-activation_bytes", "deadbeef"]
    mock_logger.warning.assert_called_once()

    # Case 5: Complete fallback. No specific key, no auth bytes.
    flags = client.get_drm_flags("book.aax", {}, "Main", None, data_dir)
    assert flags == []


# --- Error Handler Legacy Support (Lines 168-184) ---
# Tests the standard _handle_api_error method (used by older parts of your architecture)


def test_handle_api_error_strings(client):
    with pytest.raises(RateLimitError):
        client._handle_api_error(Exception("429 too many requests"))

    with pytest.raises(APIUnavailableError):
        client._handle_api_error(Exception("Server returned 502 bad gateway"))

    with pytest.raises(Exception, match="Random"):
        client._handle_api_error(Exception("Random error"))
