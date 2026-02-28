"""Deep tests for JWT token management — refresh, expiration, race conditions.

Scenarios:
  - Token not expired → return cached
  - Token expired → refresh called once
  - Concurrent callers → lock ensures single refresh
  - Refresh failure → AlorGetTokenException propagated
  - Refresh returns 500 → proper exception
  - _sign_payload uses fresh token
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from multibroker.clients.alor.exceptions import AlorGetTokenException
from multibroker.mb_client import RestCallType
from tests.conftest import _make_stub_alor_client


class TestEnsureJwtToken:
    @pytest.mark.asyncio
    async def test_returns_cached_when_not_expired(self):
        """Token issued far in the future → returns immediately, no refresh."""
        client = _make_stub_alor_client(
            jwt_token='cached_token',
            jwt_token_issued=9_999_999_999,
        )
        client._refresh_jwt = AsyncMock(return_value='should_not_call')

        token = await client._ensure_jwt_token()
        assert token == 'cached_token'
        client._refresh_jwt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refreshes_when_expired(self):
        """Token issued long ago → triggers refresh."""
        client = _make_stub_alor_client(
            jwt_token='old_token',
            jwt_token_issued=0,  # epoch → definitely expired
        )
        client._refresh_jwt = AsyncMock(return_value='new_token')

        token = await client._ensure_jwt_token()
        assert token == 'new_token'
        client._refresh_jwt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refreshes_when_token_is_none(self):
        """jwt_token=None → refresh."""
        client = _make_stub_alor_client(jwt_token=None, jwt_token_issued=0)
        client._refresh_jwt = AsyncMock(return_value='fresh')
        token = await client._ensure_jwt_token()
        assert token == 'fresh'

    @pytest.mark.asyncio
    async def test_concurrent_callers_single_refresh(self):
        """10 concurrent _ensure_jwt_token calls → only 1 _refresh_jwt call."""
        call_count = 0

        async def slow_refresh():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # simulate network delay
            client.jwt_token = 'refreshed'
            client.jwt_token_issued = 9_999_999_999
            return 'refreshed'

        client = _make_stub_alor_client(jwt_token='expired', jwt_token_issued=0)
        client._refresh_jwt = slow_refresh

        # Fire 10 concurrent calls
        results = await asyncio.gather(*[client._ensure_jwt_token() for _ in range(10)])

        assert call_count == 1, f'Expected 1 refresh, got {call_count}'
        assert all(r == 'refreshed' for r in results)

    @pytest.mark.asyncio
    async def test_refresh_failure_propagates(self):
        """_refresh_jwt raises → AlorGetTokenException propagated to all waiters."""
        client = _make_stub_alor_client(jwt_token=None, jwt_token_issued=0)
        client._refresh_jwt = AsyncMock(
            side_effect=AlorGetTokenException(status_code=503, response='Service Unavailable')
        )

        with pytest.raises(AlorGetTokenException) as exc_info:
            await client._ensure_jwt_token()
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_refresh_failure_concurrent_all_fail(self):
        """All concurrent callers see the same exception."""
        client = _make_stub_alor_client(jwt_token=None, jwt_token_issued=0)

        async def fail_refresh():
            await asyncio.sleep(0.01)
            raise AlorGetTokenException(status_code=500, response='fail')

        client._refresh_jwt = fail_refresh

        results = await asyncio.gather(
            *[client._ensure_jwt_token() for _ in range(5)],
            return_exceptions=True,
        )
        # All should fail (either the original or re-entering the lock and seeing None token again)
        for r in results:
            assert isinstance(r, AlorGetTokenException)


class TestSignPayload:
    @pytest.mark.asyncio
    async def test_sign_payload_adds_authorization_header(self):
        """_sign_payload injects Bearer token into headers."""
        client = _make_stub_alor_client(jwt_token='my_token', jwt_token_issued=9_999_999_999)
        headers = {}

        await client._sign_payload(RestCallType.GET, '/test', headers=headers)

        assert headers['Authorization'] == 'Bearer my_token'
        assert headers['Content-Type'] == 'application/json'

    @pytest.mark.asyncio
    async def test_sign_payload_refreshes_if_expired(self):
        """If token expired, _sign_payload triggers refresh before signing."""
        client = _make_stub_alor_client(jwt_token='old', jwt_token_issued=0)
        client._refresh_jwt = AsyncMock(return_value='new_token')

        # After _refresh_jwt, jwt_token should be set
        async def refresh_side_effect():
            client.jwt_token = 'new_token'
            client.jwt_token_issued = 9_999_999_999
            return 'new_token'

        client._refresh_jwt = refresh_side_effect
        headers = {}

        await client._sign_payload(RestCallType.POST, '/order', headers=headers)
        assert headers['Authorization'] == 'Bearer new_token'
