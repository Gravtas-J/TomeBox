from unittest.mock import MagicMock

import httpx
import pytest

from api.audible_client import APIUnavailableError, AudibleClient, RateLimitError


@pytest.fixture
def client():
    return AudibleClient()


@pytest.fixture
def mock_sleep(monkeypatch):
    """Prevents tests from actually pausing during retry loops."""
    sleep_mock = MagicMock()
    monkeypatch.setattr("time.sleep", sleep_mock)
    return sleep_mock


def create_http_error(status_code):
    """Helper to mock an httpx.HTTPStatusError with a specific code."""
    request = httpx.Request("GET", "https://api.audible.com")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("Error", request=request, response=response)


# --- _request_with_backoff ---


def test_backoff_success_first_try(client, mock_sleep):
    mock_func = MagicMock(return_value="success")

    result = client._request_with_backoff(mock_func)

    assert result == "success"
    assert mock_func.call_count == 1
    mock_sleep.assert_not_called()


def test_backoff_recovers_from_429(client, mock_sleep):
    # Fails twice with 429, succeeds on the third try
    mock_func = MagicMock(
        side_effect=[create_http_error(429), create_http_error(429), "success"]
    )

    result = client._request_with_backoff(mock_func, max_retries=3, base_delay=1)

    assert result == "success"
    assert mock_func.call_count == 3

    # Check exponential backoff scaling (base_delay * 2^attempt)
    # attempt 0 -> 1 * (2^0) = 1
    # attempt 1 -> 1 * (2^1) = 2
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(1)
    mock_sleep.assert_any_call(2)


def test_backoff_exhausts_retries_on_429(client, mock_sleep):
    mock_func = MagicMock(side_effect=create_http_error(429))

    with pytest.raises(RateLimitError):
        client._request_with_backoff(mock_func, max_retries=3)

    assert mock_func.call_count == 3


def test_backoff_fails_fast_on_500(client, mock_sleep):
    mock_func = MagicMock(side_effect=create_http_error(500))

    with pytest.raises(APIUnavailableError):
        client._request_with_backoff(mock_func)

    # Should fail immediately without retrying or sleeping
    assert mock_func.call_count == 1
    mock_sleep.assert_not_called()


def test_backoff_unhandled_http_error_passes_through(client, mock_sleep):
    # 404 is not in the handled lists for 429 or 50x
    mock_func = MagicMock(side_effect=create_http_error(404))

    with pytest.raises(httpx.HTTPStatusError) as exc:
        client._request_with_backoff(mock_func)

    assert exc.value.response.status_code == 404
    assert mock_func.call_count == 1


def test_backoff_generic_exception_429_parsing(client, mock_sleep):
    # A generic exception (not httpx) that contains "429" in the message
    mock_func = MagicMock(
        side_effect=[Exception("Custom wrapper threw 429 Too Many Requests"), "success"]
    )

    assert client._request_with_backoff(mock_func, max_retries=2) == "success"
    assert mock_func.call_count == 2


def test_backoff_generic_exception_500_parsing(client):
    mock_func = MagicMock(side_effect=Exception("Gateway Timeout 504"))

    with pytest.raises(APIUnavailableError):
        client._request_with_backoff(mock_func)

    assert mock_func.call_count == 1


# --- _handle_api_error ---


def test_handle_api_error_translates_429(client):
    with pytest.raises(RateLimitError):
        client._handle_api_error(create_http_error(429))


def test_handle_api_error_translates_503(client):
    with pytest.raises(APIUnavailableError):
        client._handle_api_error(create_http_error(503))


def test_handle_api_error_generic_string(client):
    with pytest.raises(RateLimitError):
        client._handle_api_error(Exception("Failed with 429 too many requests"))
