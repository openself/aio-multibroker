"""Unit-тесты для context manager (§8, G-007)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_aenter_aexit_closes_session():
    """async with AlorClient() корректно закрывает REST session."""
    with patch("multibroker.clients.alor.AlorClient.AlorClient._refresh_jwt_sync") as mock_refresh:
        mock_refresh.return_value = "fake_token"

        from multibroker.clients.alor.AlorClient import AlorClient

        client = AlorClient.__new__(AlorClient)
        # Manually init required attrs
        client.api_trace_log = False
        client.rest_session = None
        client.subscription_sets = {}
        client.ssl_context = MagicMock()
        client.is_demo = True
        client.jwt_token = "fake"
        client.jwt_token_decoded = {}
        client.jwt_token_issued = 999999999
        client._jwt_aiohttp_session = None
        client.refresh_token = "test"
        client.oauth_server = "https://oauthdev.alor.ru"
        client.rest_api_uri = "https://apidev.alor.ru/"
        client.accounts = []

        close_mock = AsyncMock()
        client.close = close_mock
        client.shutdown_websockets = AsyncMock()

        async with client as c:
            assert c is client

        close_mock.assert_awaited_once()
        client.shutdown_websockets.assert_awaited_once()


@pytest.mark.asyncio
async def test_exception_inside_context_still_closes():
    """При исключении внутри async with — ресурсы всё равно освобождаются."""
    with patch("multibroker.clients.alor.AlorClient.AlorClient._refresh_jwt_sync") as mock_refresh:
        mock_refresh.return_value = "fake_token"

        from multibroker.clients.alor.AlorClient import AlorClient

        client = AlorClient.__new__(AlorClient)
        client.api_trace_log = False
        client.rest_session = None
        client.subscription_sets = {}
        client.ssl_context = MagicMock()
        client.is_demo = True
        client.jwt_token = "fake"
        client.jwt_token_decoded = {}
        client.jwt_token_issued = 999999999
        client._jwt_aiohttp_session = None
        client.refresh_token = "test"
        client.oauth_server = "https://oauthdev.alor.ru"
        client.rest_api_uri = "https://apidev.alor.ru/"
        client.accounts = []

        close_mock = AsyncMock()
        client.close = close_mock
        client.shutdown_websockets = AsyncMock()

        with pytest.raises(ValueError):
            async with client:
                raise ValueError("test error")

        close_mock.assert_awaited_once()
