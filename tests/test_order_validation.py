"""Deep tests for order parameter validation — every order method, every edge case.

All tests must work WITHOUT real network (no __init__ JWT call).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from multibroker.clients.alor.AlorClient import AlorClient
from multibroker.clients.alor.enums import Exchange, ExecutionCondition, OrderSide
from multibroker.mb_client import MBClient
from tests.conftest import _make_stub_alor_client

# ---------------------------------------------------------------------------
# _validate_order_params — the core guard
# ---------------------------------------------------------------------------

class TestValidateOrderParams:

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError, match="symbol must not be empty"):
            AlorClient._validate_order_params(symbol='', side=OrderSide.BUY, quantity=1, portfolio='D12345')

    def test_none_side_raises(self):
        with pytest.raises(ValueError, match="side must be specified"):
            AlorClient._validate_order_params(symbol='SBER', side=None, quantity=1, portfolio='D12345')

    def test_zero_quantity_raises(self):
        with pytest.raises(ValueError, match="quantity must be > 0"):
            AlorClient._validate_order_params(symbol='SBER', side=OrderSide.BUY, quantity=0, portfolio='D12345')

    def test_negative_quantity_raises(self):
        with pytest.raises(ValueError, match="quantity must be > 0"):
            AlorClient._validate_order_params(symbol='SBER', side=OrderSide.SELL, quantity=-5, portfolio='D12345')

    def test_empty_portfolio_raises(self):
        with pytest.raises(ValueError, match="portfolio must not be empty"):
            AlorClient._validate_order_params(symbol='SBER', side=OrderSide.BUY, quantity=1, portfolio='')

    def test_valid_params_pass(self):
        AlorClient._validate_order_params(symbol='SBER', side=OrderSide.BUY, quantity=10, portfolio='D12345')

    def test_quantity_one_passes(self):
        """Edge: minimum valid quantity = 1."""
        AlorClient._validate_order_params(symbol='Si', side=OrderSide.SELL, quantity=1, portfolio='750001')

    def test_huge_quantity_passes(self):
        """Edge: very large quantity — validation doesn't cap it (broker's job)."""
        AlorClient._validate_order_params(symbol='Si', side=OrderSide.BUY, quantity=999_999, portfolio='750001')


# ---------------------------------------------------------------------------
# create_market_order validation
# ---------------------------------------------------------------------------

class TestCreateMarketOrderValidation:

    @pytest.mark.asyncio
    async def test_zero_quantity_rejected_before_network(self):
        """quantity=0 → ValueError BEFORE any network call."""
        client = _make_stub_alor_client()
        mock = AsyncMock()
        with patch.object(MBClient, "_create_rest_call", mock), pytest.raises(ValueError, match="quantity"):
            await client.create_market_order(
                portfolio="D12345", exchange=Exchange.MOEX,
                symbol="SBER", side=OrderSide.BUY, quantity=0,
            )
        mock.assert_not_awaited()  # No network call made

    @pytest.mark.asyncio
    async def test_none_side_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="side"):
            await client.create_market_order(
                portfolio="D12345", exchange=Exchange.MOEX,
                symbol="SBER", side=None, quantity=10,
            )

    @pytest.mark.asyncio
    async def test_empty_symbol_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="symbol"):
            await client.create_market_order(
                portfolio="D12345", exchange=Exchange.MOEX,
                symbol="", side=OrderSide.BUY, quantity=10,
            )


# ---------------------------------------------------------------------------
# create_limit_order validation
# ---------------------------------------------------------------------------

class TestCreateLimitOrderValidation:

    @pytest.mark.asyncio
    async def test_zero_price_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="price must be > 0"):
            await client.create_limit_order(
                portfolio="D12345", exchange=Exchange.MOEX,
                symbol="SBER", side=OrderSide.BUY, quantity=10, price=0.0,
            )

    @pytest.mark.asyncio
    async def test_negative_price_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="price must be > 0"):
            await client.create_limit_order(
                portfolio="D12345", exchange=Exchange.MOEX,
                symbol="SBER", side=OrderSide.BUY, quantity=10, price=-1.5,
            )

    @pytest.mark.asyncio
    async def test_zero_quantity_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="quantity"):
            await client.create_limit_order(
                portfolio="D12345", exchange=Exchange.MOEX,
                symbol="SBER", side=OrderSide.BUY, quantity=0, price=100.0,
            )


# ---------------------------------------------------------------------------
# create_limit_stop_order validation
# ---------------------------------------------------------------------------

class TestCreateLimitStopOrderValidation:

    @pytest.mark.asyncio
    async def test_zero_price_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="price must be > 0"):
            await client.create_limit_stop_order(
                portfolio="750001", exchange=Exchange.MOEX,
                symbol="SiH5", side=OrderSide.SELL, quantity=1,
                price=0.0, trigger_price=100.0,
                condition=ExecutionCondition.LESS_OR_EQUAL,
            )

    @pytest.mark.asyncio
    async def test_zero_trigger_price_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="Trigger price must be > 0"):
            await client.create_limit_stop_order(
                portfolio="750001", exchange=Exchange.MOEX,
                symbol="SiH5", side=OrderSide.SELL, quantity=1,
                price=100.0, trigger_price=0.0,
                condition=ExecutionCondition.LESS_OR_EQUAL,
            )

    @pytest.mark.asyncio
    async def test_none_side_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="side"):
            await client.create_limit_stop_order(
                portfolio="750001", exchange=Exchange.MOEX,
                symbol="SiH5", side=None, quantity=1,
                price=100.0, trigger_price=90.0,
                condition=ExecutionCondition.LESS_OR_EQUAL,
            )


# ---------------------------------------------------------------------------
# create_stop_order validation
# ---------------------------------------------------------------------------

class TestCreateStopOrderValidation:

    @pytest.mark.asyncio
    async def test_zero_trigger_price_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="Trigger price must be > 0"):
            await client.create_stop_order(
                portfolio="750001", exchange=Exchange.MOEX,
                symbol="SiH5", side=OrderSide.SELL, quantity=1,
                condition=ExecutionCondition.LESS, trigger_price=0.0,
            )

    @pytest.mark.asyncio
    async def test_empty_portfolio_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="portfolio"):
            await client.create_stop_order(
                portfolio="", exchange=Exchange.MOEX,
                symbol="SiH5", side=OrderSide.SELL, quantity=1,
                condition=ExecutionCondition.LESS, trigger_price=100.0,
            )


# ---------------------------------------------------------------------------
# update_market_order / update_limit_order validation
# ---------------------------------------------------------------------------

class TestUpdateOrderValidation:

    @pytest.mark.asyncio
    async def test_update_market_zero_quantity_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="quantity"):
            await client.update_market_order(
                order_id="123", portfolio="D12345", exchange=Exchange.MOEX,
                symbol="SBER", side=OrderSide.BUY, quantity=0,
            )

    @pytest.mark.asyncio
    async def test_update_limit_zero_price_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="price"):
            await client.update_limit_order(
                order_id="123", portfolio="D12345", exchange=Exchange.MOEX,
                symbol="SBER", side=OrderSide.BUY, quantity=10, price=0.0,
            )

    @pytest.mark.asyncio
    async def test_update_limit_negative_price_rejected(self):
        client = _make_stub_alor_client()
        with pytest.raises(ValueError, match="price"):
            await client.update_limit_order(
                order_id="123", portfolio="D12345", exchange=Exchange.MOEX,
                symbol="SBER", side=OrderSide.BUY, quantity=10, price=-50.0,
            )


# ---------------------------------------------------------------------------
# Regression: _get_unix_timestamp_ns
# ---------------------------------------------------------------------------

class TestTimestampNs:

    def test_timestamp_ns_sane_range(self):
        from multibroker.mb_client import MBClient
        ts = MBClient._get_unix_timestamp_ns()
        assert 1_000_000_000_000_000_000 < ts < 3_000_000_000_000_000_000, \
            f"Timestamp {ts} is outside sane nanosecond range — possible double-multiply bug"


# ---------------------------------------------------------------------------
# X-REQID presence on all mutating endpoints
# ---------------------------------------------------------------------------

class TestXReqidPresence:
    """Verify that all order-mutating methods set X-REQID in headers."""

    @pytest.mark.asyncio
    async def _capture_headers(self, method_name: str, **kwargs) -> dict:
        """Call a method on stubbed client, capture the headers dict passed to _create_rest_call."""
        client = _make_stub_alor_client()
        captured = {}

        async def spy(self_, call_type, resource, data=None, params=None,
                      headers=None, signed=False, api_variable_path=None):
            captured.update(headers or {})
            return {"status_code": 200, "headers": {}, "response": {}}

        with patch.object(MBClient, "_create_rest_call", spy):
            method = getattr(client, method_name)
            await method(**kwargs)

        return captured

    @pytest.mark.asyncio
    async def test_create_market_order_has_reqid(self):
        h = await self._capture_headers(
            "create_market_order",
            portfolio="D1", exchange=Exchange.MOEX, symbol="SBER",
            side=OrderSide.BUY, quantity=1,
        )
        assert "X-REQID" in h

    @pytest.mark.asyncio
    async def test_create_limit_order_has_reqid(self):
        h = await self._capture_headers(
            "create_limit_order",
            portfolio="D1", exchange=Exchange.MOEX, symbol="SBER",
            side=OrderSide.BUY, quantity=1, price=100.0,
        )
        assert "X-REQID" in h

    @pytest.mark.asyncio
    async def test_delete_order_has_reqid(self):
        h = await self._capture_headers(
            "delete_order",
            portfolio="D1", exchange=Exchange.MOEX, order_id="999",
        )
        assert "X-REQID" in h

    @pytest.mark.asyncio
    async def test_delete_all_orders_has_reqid(self):
        h = await self._capture_headers(
            "delete_all_orders",
            portfolio="D1", exchange=Exchange.MOEX,
        )
        assert "X-REQID" in h

    @pytest.mark.asyncio
    async def test_create_stop_order_has_reqid(self):
        h = await self._capture_headers(
            "create_stop_order",
            portfolio="750001", exchange=Exchange.MOEX, symbol="SiH5",
            side=OrderSide.SELL, quantity=1,
            condition=ExecutionCondition.LESS, trigger_price=100.0,
        )
        assert "X-REQID" in h

    @pytest.mark.asyncio
    async def test_update_market_order_has_reqid(self):
        h = await self._capture_headers(
            "update_market_order",
            order_id="123", portfolio="D1", exchange=Exchange.MOEX,
            symbol="SBER", side=OrderSide.BUY, quantity=1,
        )
        assert "X-REQID" in h

    @pytest.mark.asyncio
    async def test_update_limit_order_has_reqid(self):
        h = await self._capture_headers(
            "update_limit_order",
            order_id="123", portfolio="D1", exchange=Exchange.MOEX,
            symbol="SBER", side=OrderSide.BUY, quantity=1, price=100.0,
        )
        assert "X-REQID" in h
