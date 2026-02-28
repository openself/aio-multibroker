"""Deep tests for REST retry logic, network failures, session recreation.

Scenarios:
  - ServerDisconnectedError → session recreated, retry succeeds
  - ConnectionResetError mid-request → same
  - Multiple 500s → exhaust retries → raise last exc
  - 401 → single refresh → retry with new token
  - 401 twice → give up after AUTH_RETRY_LIMIT
  - 429 with Retry-After → respects wait
  - 400 → no retry, raises immediately
  - Timeout → retry with backoff, then fail
  - Mixed errors (500 then 401 then success)
  - ClientPayloadError (truncated response) → retry
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy

from multibroker.clients.alor.AlorClient import MAX_RETRIES, AlorClient
from multibroker.clients.alor.exceptions import (
    BrokerAuthError,
    BrokerBadRequestError,
    BrokerForbiddenError,
    BrokerNotFoundError,
    BrokerRateLimitError,
    BrokerServerError,
    BrokerTimeoutError,
)
from multibroker.mb_client import RestCallType
from tests.conftest import _make_stub_alor_client

_HEADERS = CIMultiDictProxy(CIMultiDict())


def _ok_response():
    return {"status_code": 200, "headers": _HEADERS, "response": {"ok": True}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client_with_parent_mock(side_effects: list) -> tuple[AlorClient, AsyncMock]:
    """Stub client whose super()._create_rest_call is mocked with given side effects."""
    client = _make_stub_alor_client()
    mock = AsyncMock(side_effect=side_effects)
    return client, mock


def _patch_super(client, mock):
    """Patch MBClient._create_rest_call on this specific client instance."""
    from multibroker.mb_client import MBClient
    return patch.object(MBClient, "_create_rest_call", mock)


# ---------------------------------------------------------------------------
# Network errors → session recreation + retry
# ---------------------------------------------------------------------------

class TestConnectionErrors:

    @pytest.mark.asyncio
    async def test_server_disconnected_triggers_session_recreate(self):
        """aiohttp.ServerDisconnectedError → _recreate_rest_session → retry → success."""
        client, mock = _client_with_parent_mock([
            aiohttp.ServerDisconnectedError(),
            _ok_response(),
        ])
        client._recreate_rest_session = AsyncMock()

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=1)

        assert result["status_code"] == 200
        client._recreate_rest_session.assert_awaited_once()
        assert mock.call_count == 2

    @pytest.mark.asyncio
    async def test_client_os_error_retries(self):
        """aiohttp.ClientOSError (e.g. ECONNREFUSED) → retry → success."""
        client, mock = _client_with_parent_mock([
            aiohttp.ClientOSError(111, "Connection refused"),
            _ok_response(),
        ])
        client._recreate_rest_session = AsyncMock()

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=1)

        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_connection_errors_exhaust_retries(self):
        """3x ServerDisconnectedError → BrokerTimeoutError after MAX_RETRIES."""
        client, mock = _client_with_parent_mock([
            aiohttp.ServerDisconnectedError(),
            aiohttp.ServerDisconnectedError(),
            aiohttp.ServerDisconnectedError(),
        ])
        client._recreate_rest_session = AsyncMock()

        with _patch_super(client, mock), pytest.raises(BrokerTimeoutError, match="Connection error"):
            await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=1)

        assert mock.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_client_payload_error_retries(self):
        """aiohttp.ClientPayloadError (truncated body) → retry → success."""
        client, mock = _client_with_parent_mock([
            aiohttp.ClientPayloadError("partial response"),
            _ok_response(),
        ])

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.POST, "/test", timeout_sec=1)

        assert result["status_code"] == 200


# ---------------------------------------------------------------------------
# Server 5xx → retry with backoff
# ---------------------------------------------------------------------------

class TestServerErrors:

    @pytest.mark.asyncio
    async def test_single_500_then_success(self):
        """One 500 → backoff → retry → 200."""
        client, mock = _client_with_parent_mock([
            BrokerServerError(500, {"error": "internal"}),
            _ok_response(),
        ])

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=5)

        assert result["status_code"] == 200
        assert mock.call_count == 2

    @pytest.mark.asyncio
    async def test_three_500s_raises_last(self):
        """3x 500 → exhaust retries → re-raise last BrokerServerError."""
        client, mock = _client_with_parent_mock([
            BrokerServerError(500, {"error": "1"}),
            BrokerServerError(502, {"error": "2"}),
            BrokerServerError(503, {"error": "3"}),
        ])

        with _patch_super(client, mock), pytest.raises(BrokerServerError) as exc_info:
            await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=5)

        # Last error should be the 503
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Auth errors → refresh once, then give up
# ---------------------------------------------------------------------------

class TestAuthRetry:

    @pytest.mark.asyncio
    async def test_401_refreshes_and_retries(self):
        """401 → _refresh_jwt → retry with new token → success."""
        client, mock = _client_with_parent_mock([
            BrokerAuthError(401, None),
            _ok_response(),
        ])
        client._refresh_jwt = AsyncMock(return_value="new_token")

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", signed=True, timeout_sec=5)

        assert result["status_code"] == 200
        client._refresh_jwt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_401_twice_gives_up(self):
        """Two consecutive 401s → raises after AUTH_RETRY_LIMIT (1)."""
        client, mock = _client_with_parent_mock([
            BrokerAuthError(401, None),
            BrokerAuthError(401, None),
        ])
        client._refresh_jwt = AsyncMock(return_value="new_token")

        with _patch_super(client, mock), pytest.raises(BrokerAuthError):
            await client._create_rest_call(RestCallType.GET, "/test", signed=True, timeout_sec=5)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:

    @pytest.mark.asyncio
    async def test_429_respects_retry_after_then_succeeds(self):
        """429 with Retry-After → wait → retry → success."""
        client, mock = _client_with_parent_mock([
            BrokerRateLimitError(429, None, retry_after_sec=0.01),
            _ok_response(),
        ])

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=5)

        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_429_without_retry_after_uses_backoff(self):
        """429 without Retry-After → exponential backoff → retry → success."""
        client, mock = _client_with_parent_mock([
            BrokerRateLimitError(429, None, retry_after_sec=None),
            _ok_response(),
        ])

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=5)

        assert result["status_code"] == 200


# ---------------------------------------------------------------------------
# Non-retryable errors → raise immediately, no retry
# ---------------------------------------------------------------------------

class TestNonRetryable:

    @pytest.mark.asyncio
    async def test_400_raises_immediately(self):
        client, mock = _client_with_parent_mock([
            BrokerBadRequestError(400, {"message": "bad field"}),
        ])

        with _patch_super(client, mock), pytest.raises(BrokerBadRequestError):
            await client._create_rest_call(RestCallType.POST, "/test", timeout_sec=5)

        # Only 1 attempt — no retries
        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_403_raises_immediately(self):
        client, mock = _client_with_parent_mock([
            BrokerForbiddenError(403, None),
        ])

        with _patch_super(client, mock), pytest.raises(BrokerForbiddenError):
            await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=5)

        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_404_raises_immediately(self):
        client, mock = _client_with_parent_mock([
            BrokerNotFoundError(404, None),
        ])

        with _patch_super(client, mock), pytest.raises(BrokerNotFoundError):
            await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=5)

        assert mock.call_count == 1


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

class TestTimeout:

    @pytest.mark.asyncio
    async def test_timeout_retries_then_raises(self):
        """TimeoutError on every attempt → BrokerTimeoutError."""
        client, mock = _client_with_parent_mock([
            TimeoutError(),
            TimeoutError(),
            TimeoutError(),
        ])

        with _patch_super(client, mock), pytest.raises(BrokerTimeoutError, match="timed out"):
            await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=0.01)

    @pytest.mark.asyncio
    async def test_timeout_then_success(self):
        """First call times out, second succeeds."""
        client, mock = _client_with_parent_mock([
            TimeoutError(),
            _ok_response(),
        ])

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=0.01)

        assert result["status_code"] == 200


# ---------------------------------------------------------------------------
# Mixed error sequences
# ---------------------------------------------------------------------------

class TestMixedErrors:

    @pytest.mark.asyncio
    async def test_500_then_timeout_then_success(self):
        client, mock = _client_with_parent_mock([
            BrokerServerError(500, None),
            TimeoutError(),
            _ok_response(),
        ])

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=5)

        assert result["status_code"] == 200
        assert mock.call_count == 3

    @pytest.mark.asyncio
    async def test_disconnect_then_500_then_success(self):
        client, mock = _client_with_parent_mock([
            aiohttp.ServerDisconnectedError(),
            BrokerServerError(502, None),
            _ok_response(),
        ])
        client._recreate_rest_session = AsyncMock()

        with _patch_super(client, mock):
            result = await client._create_rest_call(RestCallType.GET, "/test", timeout_sec=5)

        assert result["status_code"] == 200
        client._recreate_rest_session.assert_awaited_once()  # only on the disconnect


# ---------------------------------------------------------------------------
# X-REQID idempotency — verify headers survive retry
# ---------------------------------------------------------------------------

class TestIdempotency:

    @pytest.mark.asyncio
    async def test_x_reqid_preserved_across_retries(self):
        """X-REQID header must be identical across all retry attempts."""
        captured_headers = []

        async def capture_call(rest_call_type, resource, data, params, headers, signed, api_variable_path):
            captured_headers.append(dict(headers) if headers else {})
            if len(captured_headers) < 3:
                raise BrokerServerError(500, None)
            return _ok_response()

        client = _make_stub_alor_client()
        from multibroker.mb_client import MBClient
        with patch.object(MBClient, "_create_rest_call", side_effect=capture_call):
            headers = {"X-REQID": "test-uuid-123"}
            await client._create_rest_call(
                RestCallType.POST, "/order", headers=headers, timeout_sec=5,
            )

        assert len(captured_headers) == 3
        # Same X-REQID on every attempt
        assert all(h["X-REQID"] == "test-uuid-123" for h in captured_headers)
