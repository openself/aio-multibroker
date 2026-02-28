"""Unit-тесты для WS-подписок (§5.2, W-01..W-04, G-004)."""

import pytest

from multibroker.clients.alor.AlorWebsocket import (
    OrdersSubscription,
    PositionsSubscription,
    SummariesSubscription,
    TradesSubscription,
)
from multibroker.ws_manager import WebsocketMessage


class TestOrdersSubscription:
    def test_message_format(self):
        sub = OrdersSubscription(exchange='MOEX', portfolio='750001')
        msg = sub.get_subscription_message()
        assert msg['opcode'] == 'OrdersGetAndSubscribeV2'
        assert msg['exchange'] == 'MOEX'
        assert msg['portfolio'] == '750001'
        assert msg['format'] == 'Simple'
        assert 'orderStatuses' not in msg

    def test_message_with_statuses(self):
        sub = OrdersSubscription(exchange='MOEX', portfolio='750001', order_statuses=['working', 'filled'])
        msg = sub.get_subscription_message()
        assert msg['orderStatuses'] == ['working', 'filled']

    def test_subscription_id_is_uuid(self):
        sub = OrdersSubscription()
        sid = sub.get_subscription_id()
        assert isinstance(sid, str)
        assert len(sid) == 36  # UUID4 format


class TestTradesSubscription:
    def test_message_format(self):
        sub = TradesSubscription(exchange='MOEX', portfolio='750001', skip_history=True)
        msg = sub.get_subscription_message()
        assert msg['opcode'] == 'TradesGetAndSubscribeV2'
        assert msg['skipHistory'] is True


class TestPositionsSubscription:
    def test_message_format(self):
        sub = PositionsSubscription(exchange='MOEX', portfolio='750001')
        msg = sub.get_subscription_message()
        assert msg['opcode'] == 'PositionsGetAndSubscribeV2'
        assert msg['portfolio'] == '750001'


class TestSummariesSubscription:
    def test_message_format(self):
        sub = SummariesSubscription(exchange='MOEX', portfolio='750001')
        msg = sub.get_subscription_message()
        assert msg['opcode'] == 'SummariesGetAndSubscribeV2'


class TestCallbacks:
    @pytest.mark.asyncio
    async def test_orders_callback_receives_data(self):
        received = []

        async def on_order(data):
            received.append(data)

        sub = OrdersSubscription(callbacks=[on_order])
        msg = WebsocketMessage(subscription_id=sub.get_subscription_id(), message={'id': '123', 'status': 'filled'})
        await sub.process_message(msg)
        assert len(received) == 1
        assert received[0]['status'] == 'filled'

    @pytest.mark.asyncio
    async def test_trades_callback_receives_fill_data(self):
        received = []

        async def on_trade(data):
            received.append(data)

        sub = TradesSubscription(callbacks=[on_trade])
        msg = WebsocketMessage(
            subscription_id=sub.get_subscription_id(),
            message={'id': '456', 'price': 100.5, 'qty': 10, 'side': 'buy'},
        )
        await sub.process_message(msg)
        assert len(received) == 1
        assert received[0]['price'] == 100.5
