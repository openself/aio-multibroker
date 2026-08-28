"""Tests for aiohttp session lifecycle — creation, recreation, connector params.

Scenarios:
  - Session created lazily on first REST call
  - Session reused across calls
  - _recreate_rest_session closes old, next call creates new
  - _recreate_rest_session identity-check: stale_session no longer matches
    self.rest_session → no-op (another coroutine already replaced it)
  - close() with no session → no-op
  - close() with existing session → closes it
  - TCPConnector params (keepalive, limit, cleanup)
  - Shutdown with no WS managers started → no crash
  - Shutdown with websocket_mgr=None → no crash
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from multibroker.mb_client import MBClient, RestCallType, SubscriptionSet
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

    @pytest.mark.asyncio
    async def test_recreate_with_matching_stale_session_closes_it(self):
        """stale_session identity matches the live session → closes it, as usual."""
        client = _make_stub_alor_client()
        session = client._get_rest_session()

        await client._recreate_rest_session(stale_session=session)

        assert session.closed
        assert client.rest_session is None

    @pytest.mark.asyncio
    async def test_recreate_with_stale_session_already_replaced_is_noop(self):
        """rest_session is shared across coroutines: if another coroutine already
        replaced it by the time we're called, our stale reference must not close
        the fresh session or clear it out from under that coroutine."""
        client = _make_stub_alor_client()
        old_session = client._get_rest_session()
        new_session = MagicMock(spec=aiohttp.ClientSession)
        new_session.closed = False
        client.rest_session = new_session  # another coroutine already recreated it

        await client._recreate_rest_session(stale_session=old_session)

        assert client.rest_session is new_session
        assert not old_session.closed
        await old_session.close()

    @pytest.mark.asyncio
    async def test_concurrent_get_rest_session_during_close_await_does_not_get_clobbered(self):
        """Real two-coroutine interleaving (not a single-task simulation): aiohttp
        flips a session's `.closed` to True synchronously, before the awaited
        close() actually finishes tearing down connections. While
        _recreate_rest_session is suspended inside that await, a concurrent,
        unlocked _get_rest_session() call can observe `.closed == True` and
        install a replacement session — which must survive once the awaited
        close() resumes and completes."""
        client = _make_stub_alor_client()
        stale_session = MagicMock(spec=aiohttp.ClientSession)
        stale_session.closed = False
        entered_close = asyncio.Event()
        resume_close = asyncio.Event()

        async def fake_close():
            stale_session.closed = True  # flips synchronously, before teardown "finishes"
            entered_close.set()
            await resume_close.wait()  # simulate close() still tearing down connections

        stale_session.close = fake_close
        client.rest_session = stale_session

        recreate_task = asyncio.create_task(client._recreate_rest_session(stale_session=stale_session))
        await entered_close.wait()

        new_session = client._get_rest_session()  # concurrent, unlocked
        assert new_session is not stale_session

        resume_close.set()
        await recreate_task

        assert client.rest_session is new_session
        await new_session.close()


class TestConcurrentSessionRecreation:
    """Exercise the real (unmocked) MBClient._create_rest_call, since patching it
    wholesale — as tests/test_retry_and_network.py does for AlorClient's retry
    wrapper — bypasses the identity-checked recreation entirely.

    Calling `client._create_rest_call(...)` on an AlorClient instance resolves
    to AlorClient's retry wrapper, not this method, so these call
    `MBClient._create_rest_call(client, ...)` explicitly (unbound) to reach the
    real base implementation without going through a real network call on retry.
    """

    @pytest.mark.asyncio
    async def test_connection_error_recreates_the_session_it_actually_failed_on(self):
        """A same-session failure closes that session and clears rest_session."""

        class _FailingRequest:
            async def __aenter__(self):
                raise aiohttp.ServerDisconnectedError()

            async def __aexit__(self, *args):
                return False

        client = _make_stub_alor_client()
        session = MagicMock(spec=aiohttp.ClientSession)
        session.closed = False
        session.get = MagicMock(return_value=_FailingRequest())
        session.close = AsyncMock()
        client.rest_session = session

        with pytest.raises(aiohttp.ServerDisconnectedError):
            await MBClient._create_rest_call(client, RestCallType.GET, '/test')

        session.close.assert_awaited_once()
        assert client.rest_session is None

    @pytest.mark.asyncio
    async def test_failure_on_already_replaced_session_does_not_touch_the_new_one(self):
        """Simulates the race the identity check exists for: while this call is
        failing on session S1, another coroutine has already replaced it with
        S2 by the time we get to recreate. S2 must survive untouched."""
        replacement_session = MagicMock(spec=aiohttp.ClientSession)
        replacement_session.closed = False

        client = _make_stub_alor_client()

        class _RacingFailure:
            async def __aenter__(self):
                client.rest_session = replacement_session
                raise aiohttp.ServerDisconnectedError()

            async def __aexit__(self, *args):
                return False

        original_session = MagicMock(spec=aiohttp.ClientSession)
        original_session.closed = False
        original_session.get = MagicMock(return_value=_RacingFailure())
        original_session.close = AsyncMock()
        client.rest_session = original_session

        with pytest.raises(aiohttp.ServerDisconnectedError):
            await MBClient._create_rest_call(client, RestCallType.GET, '/test')

        original_session.close.assert_not_awaited()
        assert client.rest_session is replacement_session

    @pytest.mark.asyncio
    async def test_original_exception_propagates_even_if_recreate_itself_fails(self):
        """If _recreate_rest_session's own cleanup raises, the original
        connection failure must still propagate — cleanup errors must never
        mask it."""

        class _FailingRequest:
            async def __aenter__(self):
                raise aiohttp.ServerDisconnectedError()

            async def __aexit__(self, *args):
                return False

        client = _make_stub_alor_client()
        session = MagicMock(spec=aiohttp.ClientSession)
        session.closed = False
        session.get = MagicMock(return_value=_FailingRequest())
        session.close = AsyncMock(side_effect=RuntimeError('close blew up'))
        client.rest_session = session

        with pytest.raises(aiohttp.ServerDisconnectedError):
            await MBClient._create_rest_call(client, RestCallType.GET, '/test')


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

    @pytest.mark.asyncio
    async def test_concurrent_get_rest_session_during_close_does_not_get_clobbered(self):
        """Same race as TestSessionRecreation's interleaving test, but through
        close(): a concurrent, unlocked _get_rest_session() call installing a
        replacement while close() is still awaiting the stale session's
        close() must not have that replacement cleared out from under it."""
        client = _make_stub_alor_client()
        stale_session = MagicMock(spec=aiohttp.ClientSession)
        stale_session.closed = False
        entered_close = asyncio.Event()
        resume_close = asyncio.Event()

        async def fake_close():
            stale_session.closed = True
            entered_close.set()
            await resume_close.wait()

        stale_session.close = fake_close
        client.rest_session = stale_session

        close_task = asyncio.create_task(client.close())
        await entered_close.wait()

        new_session = client._get_rest_session()
        assert new_session is not stale_session

        resume_close.set()
        await close_task

        assert client.rest_session is new_session
        await new_session.close()


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
