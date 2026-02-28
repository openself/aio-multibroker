"""Tests for aiohttp session lifecycle — creation, recreation, connector params.

Scenarios:
  - Session created lazily on first REST call
  - Session reused across calls
  - _recreate_rest_session closes old, next call creates new
  - close() with no session → no-op
  - close() with existing session → closes it
  - TCPConnector params (keepalive, limit, cleanup)
  - Shutdown with no WS managers started → no crash
  - Shutdown with websocket_mgr=None → no crash
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from multibroker.mb_client import SubscriptionSet
from tests.conftest import _make_stub_alor_client


class TestSessionCreation:
    @pytest.mark.asyncio
    async def test_session_created_lazily(self):
        """rest_session is None until first _get_rest_session() call."""
        client = _make_stub_alor_client()
        assert client.rest_session is None

        session = client._get_rest_session()
        assert session is not None
        assert isinstance(session, aiohttp.ClientSession)
        await session.close()

    @pytest.mark.asyncio
    async def test_session_reused(self):
        """Second call to _get_rest_session returns same object."""
        client = _make_stub_alor_client()
        s1 = client._get_rest_session()
        s2 = client._get_rest_session()
        assert s1 is s2
        await s1.close()

    @pytest.mark.asyncio
    async def test_connector_has_correct_params(self):
        """TCPConnector created with keepalive_timeout=30, limit=30."""
        client = _make_stub_alor_client()
        session = client._get_rest_session()
        connector = session.connector

        assert connector._limit == 30
        assert connector._keepalive_timeout == 30
        await session.close()


class TestSessionRecreation:
    @pytest.mark.asyncio
    async def test_recreate_closes_old_session(self):
        """_recreate_rest_session closes existing session, next call creates new."""
        client = _make_stub_alor_client()
        old_session = client._get_rest_session()
        assert not old_session.closed

        await client._recreate_rest_session()
        assert old_session.closed
        assert client.rest_session is None

        new_session = client._get_rest_session()
        assert new_session is not old_session

    @pytest.mark.asyncio
    async def test_recreate_when_no_session(self):
        """_recreate_rest_session with rest_session=None → no-op, no crash."""
        client = _make_stub_alor_client()
        client.rest_session = None
        # Should not raise
        await client._recreate_rest_session()
        assert client.rest_session is None


class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_no_session(self):
        """close() when rest_session is None → safe no-op."""
        client = _make_stub_alor_client()
        client.rest_session = None
        await client.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_close_closes_rest_session(self):
        """close() closes rest_session and jwt_session."""
        client = _make_stub_alor_client()
        _ = client._get_rest_session()  # Force creation
        assert client.rest_session is not None

        await client.close()
        # Session should be closed
        assert client.rest_session is None or client.rest_session.closed

    @pytest.mark.asyncio
    async def test_close_closes_jwt_session(self):
        """close() closes _jwt_aiohttp_session if open."""
        client = _make_stub_alor_client()
        client._jwt_aiohttp_session = aiohttp.ClientSession()
        assert not client._jwt_aiohttp_session.closed

        await client.close()
        assert client._jwt_aiohttp_session is None


class TestShutdownWebsockets:
    @pytest.mark.asyncio
    async def test_shutdown_empty_subscription_sets(self):
        """shutdown_websockets with no subscription sets → no crash."""
        client = _make_stub_alor_client()
        client.subscription_sets = {}
        await client.shutdown_websockets()

    @pytest.mark.asyncio
    async def test_shutdown_with_none_websocket_mgr(self):
        """shutdown_websockets when websocket_mgr is None → skip, no crash."""
        client = _make_stub_alor_client()
        ss = SubscriptionSet(subscriptions=[])
        ss.websocket_mgr = None
        client.subscription_sets = {0: ss}

        await client.shutdown_websockets()  # Must not raise

    @pytest.mark.asyncio
    async def test_shutdown_calls_mgr_shutdown(self):
        """shutdown_websockets calls shutdown() on each WebsocketMgr."""
        client = _make_stub_alor_client()
        mock_mgr = AsyncMock()
        mock_mgr.mode = MagicMock()
        ss = SubscriptionSet(subscriptions=[])
        ss.websocket_mgr = mock_mgr
        client.subscription_sets = {0: ss}

        await client.shutdown_websockets()
        mock_mgr.shutdown.assert_awaited_once()
