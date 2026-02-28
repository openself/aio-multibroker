"""Shared fixtures for all test modules."""

from __future__ import annotations

import asyncio
import ssl
from unittest.mock import MagicMock

import pytest

from multibroker.clients.alor.AlorClient import AlorClient

# ---------------------------------------------------------------------------
# Stub AlorClient — bypasses real __init__ (no JWT HTTP call)
# ---------------------------------------------------------------------------


def _make_stub_alor_client(**overrides):
    """Create an AlorClient instance without calling __init__ (no real network)."""

    client = AlorClient.__new__(AlorClient)
    defaults = dict(
        api_trace_log=False,
        rest_session=None,
        subscription_sets={},
        ssl_context=MagicMock(spec=ssl.SSLContext),
        is_demo=True,
        jwt_token='stub_jwt_token',
        jwt_token_decoded={},
        jwt_token_issued=9_999_999_999,  # far future → token never expires
        _jwt_aiohttp_session=None,
        _jwt_refresh_lock=asyncio.Lock(),
        refresh_token='stub_refresh',
        oauth_server='https://oauthdev.alor.ru',
        rest_api_uri='https://apidev.alor.ru/',
        accounts=[],
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(client, k, v)
    return client


@pytest.fixture
def stub_client():
    """Ready-to-use AlorClient stub with no real network."""
    return _make_stub_alor_client()
