"""Deep tests for WebSocket message processing, edge cases, malformed data.

Scenarios:
  - Malformed JSON from Alor
  - Missing guid/data fields
  - httpCode 200 / 401 / 400 / unknown
  - BINARY/CLOSING/ERROR frames in aiohttp WS
  - Orphaned subscription (guid mismatch)
  - Callback exception isolation (one bad callback doesn't kill others)
  - Multiple callbacks — all fire
  - Empty callbacks list
  - None callbacks
  - Heartbeat / ping frames
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from multibroker.clients.alor.AlorWebsocket import AlorSubscription, AlorWebsocket
from multibroker.exceptions import WebsocketClosed, WebsocketError, WebsocketReconnectionException
from multibroker.ws_manager import (
    AiohttpWebsocket,
    WebsocketMessage,
    WebsocketMgr,
)

# ---------------------------------------------------------------------------
# AlorWebsocket._process_message — various Alor response shapes
# ---------------------------------------------------------------------------

class TestAlorProcessMessage:
    """Unit tests for AlorWebsocket._process_message."""

    def _make_ws_mgr(self, subscriptions=None):
        """Create an AlorWebsocket with mock AlorClient."""
        mock_client = AsyncMock()
        mock_client._ensure_jwt_token = AsyncMock(return_value="test_token")
        ws = AlorWebsocket(
            subscriptions=subscriptions or [],
            alor_client=mock_client,
            is_demo=True,
        )
        ws.publish_message = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_valid_data_message(self):
        """Normal data message → publish_message called."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"guid": "abc-123", "data": {"price": 100.5}})
        await ws._process_message(MagicMock(), msg)

        ws.publish_message.assert_awaited_once()
        call_arg = ws.publish_message.call_args[0][0]
        assert call_arg.subscription_id == "abc-123"
        assert call_arg.message["price"] == 100.5

    @pytest.mark.asyncio
    async def test_http_code_200_no_publish(self):
        """httpCode 200 → log, no publish."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"httpCode": 200, "message": "OK"})
        await ws._process_message(MagicMock(), msg)
        ws.publish_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_http_code_401_raises_reconnect(self):
        """httpCode 401 → WebsocketReconnectionException (token expired)."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"httpCode": 401, "message": "Unauthorized"})
        with pytest.raises(WebsocketReconnectionException):
            await ws._process_message(MagicMock(), msg)

    @pytest.mark.asyncio
    async def test_http_code_400_logs_error_no_crash(self):
        """httpCode 400 → log error, continue (don't kill WS)."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"httpCode": 400, "message": "Bad subscription"})
        # Should NOT raise
        await ws._process_message(MagicMock(), msg)
        ws.publish_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_http_code_500_logs_error_no_crash(self):
        """httpCode 500 → log, do NOT crash the WS."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"httpCode": 500, "message": "Internal"})
        await ws._process_message(MagicMock(), msg)
        ws.publish_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_guid_skips(self):
        """Message with data but no guid → skip, no crash."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"data": {"price": 100}})
        await ws._process_message(MagicMock(), msg)
        ws.publish_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_data_skips(self):
        """Message with guid but no data → skip, no crash."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"guid": "abc-123"})
        await ws._process_message(MagicMock(), msg)
        ws.publish_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_object_skips(self):
        """Empty JSON object → skip."""
        ws = self._make_ws_mgr()
        await ws._process_message(MagicMock(), "{}")
        ws.publish_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_malformed_json_raises(self):
        """Broken JSON → json.JSONDecodeError → propagates (kills main_loop → reconnect)."""
        ws = self._make_ws_mgr()
        with pytest.raises(json.JSONDecodeError):
            await ws._process_message(MagicMock(), "not json{{{")

    @pytest.mark.asyncio
    async def test_extra_fields_ignored(self):
        """Message with extra unknown fields → still processes guid/data correctly."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"guid": "x", "data": {"a": 1}, "extra": "ignored", "version": 42})
        await ws._process_message(MagicMock(), msg)
        ws.publish_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_data_is_list(self):
        """data field is a list (Alor can send arrays) → still published."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"guid": "x", "data": [{"id": 1}, {"id": 2}]})
        await ws._process_message(MagicMock(), msg)
        call_arg = ws.publish_message.call_args[0][0]
        assert isinstance(call_arg.message, list)
        assert len(call_arg.message) == 2

    @pytest.mark.asyncio
    async def test_data_is_string(self):
        """data field is a plain string → published as-is."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"guid": "x", "data": "heartbeat"})
        await ws._process_message(MagicMock(), msg)
        call_arg = ws.publish_message.call_args[0][0]
        assert call_arg.message == "heartbeat"

    @pytest.mark.asyncio
    async def test_null_data_skips(self):
        """data is explicitly null → treated as missing."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"guid": "x", "data": None})
        await ws._process_message(MagicMock(), msg)
        ws.publish_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_http_code_zero(self):
        """httpCode: 0 → non-200, non-401, log error, no crash."""
        ws = self._make_ws_mgr()
        msg = json.dumps({"httpCode": 0, "message": "weird"})
        await ws._process_message(MagicMock(), msg)
        ws.publish_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# AiohttpWebsocket.receive — frame types
# ---------------------------------------------------------------------------

class TestAiohttpWebsocketReceive:
    """Tests for the receive() loop that filters non-text frames."""

    def _make_ws(self):
        ws = AiohttpWebsocket("wss://fake")
        ws.ws = MagicMock()  # pretend connected
        return ws

    def _msg(self, msg_type, data=""):
        m = MagicMock()
        m.type = msg_type
        m.data = data
        return m

    @pytest.mark.asyncio
    async def test_text_message_returned(self):
        ws = self._make_ws()
        ws.ws.receive = AsyncMock(return_value=self._msg(aiohttp.WSMsgType.TEXT, '{"ok":true}'))
        result = await ws.receive()
        assert result == '{"ok":true}'

    @pytest.mark.asyncio
    async def test_close_cmd_raises_websocket_closed(self):
        ws = self._make_ws()
        ws.ws.receive = AsyncMock(return_value=self._msg(aiohttp.WSMsgType.TEXT, "close cmd"))
        with pytest.raises(WebsocketClosed):
            await ws.receive()

    @pytest.mark.asyncio
    async def test_closed_frame_raises(self):
        ws = self._make_ws()
        ws.ws.receive = AsyncMock(return_value=self._msg(aiohttp.WSMsgType.CLOSED, "gone"))
        with pytest.raises(WebsocketClosed, match="closed"):
            await ws.receive()

    @pytest.mark.asyncio
    async def test_closing_frame_raises(self):
        ws = self._make_ws()
        ws.ws.receive = AsyncMock(return_value=self._msg(aiohttp.WSMsgType.CLOSING, "bye"))
        with pytest.raises(WebsocketClosed, match="closing"):
            await ws.receive()

    @pytest.mark.asyncio
    async def test_error_frame_raises(self):
        ws = self._make_ws()
        ws.ws.receive = AsyncMock(return_value=self._msg(aiohttp.WSMsgType.ERROR, "err"))
        with pytest.raises(WebsocketError):
            await ws.receive()

    @pytest.mark.asyncio
    async def test_binary_frame_skipped_text_returned(self):
        """BINARY frame → skip → next TEXT frame → returned."""
        ws = self._make_ws()
        ws.ws.receive = AsyncMock(side_effect=[
            self._msg(aiohttp.WSMsgType.BINARY, b"\x00\x01"),
            self._msg(aiohttp.WSMsgType.TEXT, '{"data":1}'),
        ])
        result = await ws.receive()
        assert result == '{"data":1}'
        assert ws.ws.receive.call_count == 2

    @pytest.mark.asyncio
    async def test_multiple_non_text_then_text(self):
        """3 non-text frames → all skipped → text returned."""
        ws = self._make_ws()
        ws.ws.receive = AsyncMock(side_effect=[
            self._msg(aiohttp.WSMsgType.BINARY, b""),
            self._msg(aiohttp.WSMsgType.BINARY, b""),
            self._msg(aiohttp.WSMsgType.BINARY, b""),
            self._msg(aiohttp.WSMsgType.TEXT, '{"ok":true}'),
        ])
        result = await ws.receive()
        assert result == '{"ok":true}'
        assert ws.ws.receive.call_count == 4

    @pytest.mark.asyncio
    async def test_receive_on_none_ws_raises(self):
        """receive() when ws=None → MBException."""
        from multibroker.exceptions import MBException
        ws = AiohttpWebsocket("wss://fake")
        ws.ws = None
        with pytest.raises(MBException, match="not open"):
            await ws.receive()


# ---------------------------------------------------------------------------
# Callback isolation
# ---------------------------------------------------------------------------

class TestCallbackIsolation:
    """Tests that a failing callback doesn't affect others or kill the WS."""

    @pytest.mark.asyncio
    async def test_one_bad_callback_doesnt_block_others(self):
        """If callback 1 raises, callback 2 still fires."""
        results = []

        async def good_callback(data):
            results.append(data)

        async def bad_callback(data):
            raise ValueError("bug in user code")

        sub = AlorSubscription(callbacks=[bad_callback, good_callback])
        msg = WebsocketMessage(subscription_id=sub.get_subscription_id(), message={"price": 42})

        # Should NOT raise despite bad_callback
        await sub.process_message(msg)
        assert len(results) == 1
        assert results[0]["price"] == 42

    @pytest.mark.asyncio
    async def test_all_callbacks_fail_still_no_crash(self):
        """All callbacks raise → logged, no exception propagated."""
        async def bad1(data):
            raise RuntimeError("fail1")

        async def bad2(data):
            raise TypeError("fail2")

        sub = AlorSubscription(callbacks=[bad1, bad2])
        msg = WebsocketMessage(subscription_id=sub.get_subscription_id(), message={})
        # Should NOT raise
        await sub.process_message(msg)

    @pytest.mark.asyncio
    async def test_no_callbacks_no_crash(self):
        """Subscription with no callbacks → process_message is a no-op."""
        sub = AlorSubscription(callbacks=None)
        msg = WebsocketMessage(subscription_id=sub.get_subscription_id(), message={"x": 1})
        await sub.process_message(msg)  # Should not raise

    @pytest.mark.asyncio
    async def test_empty_callbacks_list_no_crash(self):
        """Subscription with empty [] callbacks."""
        sub = AlorSubscription(callbacks=[])
        msg = WebsocketMessage(subscription_id=sub.get_subscription_id(), message={"x": 1})
        await sub.process_message(msg)

    @pytest.mark.asyncio
    async def test_three_callbacks_all_fire(self):
        """Three valid callbacks → all three receive the message."""
        fired = []

        async def cb1(data): fired.append("cb1")
        async def cb2(data): fired.append("cb2")
        async def cb3(data): fired.append("cb3")

        sub = AlorSubscription(callbacks=[cb1, cb2, cb3])
        msg = WebsocketMessage(subscription_id=sub.get_subscription_id(), message={"x": 1})
        await sub.process_message(msg)

        assert set(fired) == {"cb1", "cb2", "cb3"}


# ---------------------------------------------------------------------------
# Orphaned messages (guid mismatch)
# ---------------------------------------------------------------------------

class TestOrphanedMessages:
    """publish_message with unknown subscription_id."""

    @pytest.mark.asyncio
    async def test_orphaned_message_logged_not_crashed(self, caplog):
        """Message with unknown guid → LOG.error, no exception."""
        sub = AlorSubscription(callbacks=[AsyncMock()])
        mock_client = AsyncMock()
        mock_client._ensure_jwt_token = AsyncMock(return_value="tok")
        ws = AlorWebsocket(subscriptions=[sub], alor_client=mock_client, is_demo=True)

        orphan = WebsocketMessage(subscription_id="unknown-guid", message={"data": "lost"})

        with caplog.at_level(logging.ERROR):
            await ws.publish_message(orphan)

        assert "Orphaned" in caplog.text or "not found" in caplog.text

    @pytest.mark.asyncio
    async def test_valid_guid_reaches_callback(self):
        """Message with matching guid → callback fires."""
        received = []

        async def on_data(data):
            received.append(data)

        sub = AlorSubscription(callbacks=[on_data])
        mock_client = AsyncMock()
        mock_client._ensure_jwt_token = AsyncMock(return_value="tok")
        ws = AlorWebsocket(subscriptions=[sub], alor_client=mock_client, is_demo=True)

        valid = WebsocketMessage(subscription_id=sub.get_subscription_id(), message={"price": 99})
        await ws.publish_message(valid)

        assert len(received) == 1
        assert received[0]["price"] == 99


# ---------------------------------------------------------------------------
# WebsocketMgr._is_recoverable_exception
# ---------------------------------------------------------------------------

class TestRecoverableException:

    def test_connection_closed_error_is_recoverable(self):
        import websockets
        exc = websockets.ConnectionClosedError(None, None)
        assert WebsocketMgr._is_recoverable_exception(exc) is True

    def test_connection_reset_is_recoverable(self):
        exc = ConnectionResetError("reset")
        assert WebsocketMgr._is_recoverable_exception(exc) is True

    def test_websocket_closed_is_recoverable(self):
        exc = WebsocketClosed("closed")
        assert WebsocketMgr._is_recoverable_exception(exc) is True

    def test_websocket_reconnection_is_recoverable(self):
        exc = WebsocketReconnectionException("reconnect")
        assert WebsocketMgr._is_recoverable_exception(exc) is True

    def test_value_error_is_not_recoverable(self):
        exc = ValueError("bug")
        assert WebsocketMgr._is_recoverable_exception(exc) is False

    def test_runtime_error_is_not_recoverable(self):
        exc = RuntimeError("crash")
        assert WebsocketMgr._is_recoverable_exception(exc) is False

    def test_exception_group_all_recoverable(self):
        exc = ExceptionGroup("ws errors", [
            WebsocketClosed("a"),
            ConnectionResetError("b"),
        ])
        assert WebsocketMgr._is_recoverable_exception(exc) is True

    def test_exception_group_mixed_not_recoverable(self):
        exc = ExceptionGroup("mixed", [
            WebsocketClosed("a"),
            ValueError("bug"),
        ])
        assert WebsocketMgr._is_recoverable_exception(exc) is False

    def test_nested_exception_group(self):
        inner = ExceptionGroup("inner", [WebsocketClosed("a")])
        outer = ExceptionGroup("outer", [inner, ConnectionResetError("b")])
        assert WebsocketMgr._is_recoverable_exception(outer) is True

    def test_nested_exception_group_with_fatal(self):
        inner = ExceptionGroup("inner", [ValueError("bug")])
        outer = ExceptionGroup("outer", [inner, WebsocketClosed("a")])
        assert WebsocketMgr._is_recoverable_exception(outer) is False
