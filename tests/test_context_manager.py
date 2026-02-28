"""Unit-тесты для context manager (§8, G-007)."""

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import _make_stub_alor_client


def _make_closable_client() -> object:
    """Stub-клиент с замоканными close / shutdown_websockets."""
    client = _make_stub_alor_client()
    client.close = AsyncMock()
    client.shutdown_websockets = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_aenter_aexit_closes_session():
    """async with AlorClient() корректно закрывает REST session."""
    with patch('multibroker.clients.alor.AlorClient.AlorClient._refresh_jwt_sync'):
        client = _make_closable_client()

        async with client as c:
            assert c is client

        client.close.assert_awaited_once()
        client.shutdown_websockets.assert_awaited_once()


@pytest.mark.asyncio
async def test_exception_inside_context_still_closes():
    """При исключении внутри async with — ресурсы всё равно освобождаются."""
    with patch('multibroker.clients.alor.AlorClient.AlorClient._refresh_jwt_sync'):
        client = _make_closable_client()

        with pytest.raises(ValueError):
            async with client:
                raise ValueError('test error')

        client.close.assert_awaited_once()
