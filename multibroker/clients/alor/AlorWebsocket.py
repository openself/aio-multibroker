from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from multibroker.clients.alor.exceptions import AlorException
from multibroker.exceptions import WebsocketReconnectionException
from multibroker.ws_manager import Subscription, Websocket, WebsocketMessage, WebsocketMgr

if TYPE_CHECKING:
    from multibroker.clients.alor.AlorClient import AlorClient

LOG = logging.getLogger(__name__)


class AlorWebsocket(WebsocketMgr):
    """Alor WS manager — delegates JWT management to AlorClient (single source of truth, §12.2)."""

    jwt_token_ttl = 60

    def __init__(
        self,
        subscriptions: list[Subscription],
        ssl_context=None,
        alor_client: AlorClient | None = None,
        is_demo: bool = False,
    ) -> None:
        ws_server = f'wss://api{"dev" if is_demo else ""}.alor.ru/ws'

        super().__init__(
            websocket_uri=ws_server,
            subscriptions=subscriptions,
            ssl_context=ssl_context,
            builtin_ping_interval=None,
            auto_reconnect=True,
            periodic_timeout_sec=5,
        )
        self._alor_client = alor_client

    def get_websocket(self) -> Websocket:
        return self.get_aiohttp_websocket()

    async def _get_token(self) -> str:
        """Get JWT token from the shared AlorClient instance (async-safe)."""
        if self._alor_client is None:
            raise AlorException('AlorWebsocket requires an AlorClient instance for JWT management')
        return await self._alor_client._ensure_jwt_token()

    async def send_subscription_message(self, subscriptions: list[Subscription]):
        token = await self._get_token()
        for subscription in subscriptions:
            LOG.debug(f'> {subscription.get_subscription_message()}')
            message = subscription.get_subscription_message()
            message['token'] = token
            message['guid'] = subscription.subscription_id

            await self.websocket.send(json.dumps(message))

    async def send_unsubscription_message(self, subscriptions: list[Subscription]):
        token = await self._get_token()
        for subscription in subscriptions:
            LOG.debug(f'> unsubscribe {subscription.subscription_id}')
            message = {
                'opcode': 'unsubscribe',
                'guid': subscription.subscription_id,
                'token': token,
            }

            await self.websocket.send(json.dumps(message))

    async def _process_message(self, websocket: Websocket, message: str) -> None:
        parsed = json.loads(message)

        http_code = parsed.get('httpCode')
        if http_code is not None:
            if http_code == 200:
                LOG.info('handled successfully')
                return

            # 401 = expired token during subscribe. Log and trigger reconnect
            # so the WS re-subscribes with a fresh token.
            if http_code == 401:
                LOG.warning(f'WS auth error (401), will reconnect: {parsed}')
                raise WebsocketReconnectionException

            # Other non-200 codes (400 bad params, etc.) — log as error but
            # do NOT crash the entire WS. One bad subscription must not kill
            # bars + orders + positions.
            LOG.error(f'WS non-200 response (httpCode={http_code}): {parsed}')
            return

        guid = parsed.get('guid')
        data = parsed.get('data')
        if guid is None or data is None:
            LOG.warning(f"WS message missing 'guid' or 'data', skipping: {parsed}")
            return

        await self.publish_message(WebsocketMessage(subscription_id=guid, message=data))


class AlorSubscription(Subscription):
    def __init__(self, callbacks: list[Callable[[dict], Any]] | None = None):
        super().__init__(callbacks)
        self.get_subscription_id()

    @staticmethod
    def get_channel_name():
        pass

    def get_subscription_message(self, **kwargs) -> dict:
        pass

    def construct_subscription_id(self) -> Any:
        return str(uuid4())


class BarsSubscription(AlorSubscription):
    """Подписка на историю цен (свечи) для выбранных биржи и финансового инструмента.

    :param exchange: Биржа 'MOEX' или 'SPBX'
    :param symbol: Тикер
    :param tf: Длительность временного интервала в секундах или код ("D", "W", "M", "Y")
    :param seconds_from: Дата и время UTC в секундах для первого запрашиваемого бара
    :param skip_history: True — только новые данные, False — включая историю
    :param frequency: Максимальная частота отдачи данных сервером в мс
    :param data_format: Формат принимаемых данных 'Simple', 'Slim', 'Heavy'
    """

    def __init__(
        self,
        symbol: str = '',
        tf: str = '',
        seconds_from: int = 0,
        skip_history: bool = True,
        split_adjust: bool = False,
        exchange: str = '',
        instrument_group: str = '',
        frequency: int = 1_000_000_000,
        data_format: str = 'Simple',
        callbacks: list[Callable[[dict], Any]] | None = None,
    ):
        super().__init__(callbacks)

        self.symbol = symbol
        self.tf = str(tf)
        self.seconds_from = seconds_from
        self.skip_history = skip_history
        self.split_adjust = split_adjust
        self.exchange = exchange
        self.instrument_group = instrument_group
        self.frequency = frequency
        self.data_format = data_format

    def get_subscription_message(self, **kwargs) -> dict:
        return {
            'opcode': 'BarsGetAndSubscribe',
            'code': self.symbol,
            'tf': self.tf,
            'from': self.seconds_from,
            'skipHistory': self.skip_history,
            'splitAdjust': self.split_adjust,
            'exchange': self.exchange,
            'instrumentGroup': self.instrument_group,
            'frequency': self.frequency,
            'format': self.data_format,
        }


class OrdersSubscription(AlorSubscription):
    """WS подписка на все заявки по портфелю (W-01).

    Opcode: OrdersGetAndSubscribeV2

    Callback получает dict с полями (формат Simple):
        id, symbol, brokerSymbol, portfolio, exchange, type, side, status,
        transTime, updateTime, qtyUnits, qtyBatch, qty, filledQtyUnits,
        filledQtyBatch, filled, price, existing, timeInForce, volume
    """

    def __init__(
        self,
        exchange: str = 'MOEX',
        portfolio: str = '',
        order_statuses: list[str] | None = None,
        skip_history: bool = False,
        data_format: str = 'Simple',
        callbacks: list[Callable[[dict], Any]] | None = None,
    ) -> None:
        super().__init__(callbacks)
        self.exchange = exchange
        self.portfolio = portfolio
        self.order_statuses = order_statuses or []
        self.skip_history = skip_history
        self.data_format = data_format

    def get_subscription_message(self, **kwargs) -> dict:
        msg: dict[str, Any] = {
            'opcode': 'OrdersGetAndSubscribeV2',
            'exchange': self.exchange,
            'portfolio': self.portfolio,
            'skipHistory': self.skip_history,
            'format': self.data_format,
        }
        if self.order_statuses:
            msg['orderStatuses'] = self.order_statuses
        return msg


class TradesSubscription(AlorSubscription):
    """WS подписка на все сделки по портфелю (W-02).

    Opcode: TradesGetAndSubscribeV2

    Callback получает dict с полями (формат Simple):
        id, orderno, symbol, brokerSymbol, exchange, date, board,
        qtyUnits, qtyBatch, qty, price, side, existing, commission, volume, value
    """

    def __init__(
        self,
        exchange: str = 'MOEX',
        portfolio: str = '',
        skip_history: bool = False,
        data_format: str = 'Simple',
        callbacks: list[Callable[[dict], Any]] | None = None,
    ) -> None:
        super().__init__(callbacks)
        self.exchange = exchange
        self.portfolio = portfolio
        self.skip_history = skip_history
        self.data_format = data_format

    def get_subscription_message(self, **kwargs) -> dict:
        return {
            'opcode': 'TradesGetAndSubscribeV2',
            'exchange': self.exchange,
            'portfolio': self.portfolio,
            'skipHistory': self.skip_history,
            'format': self.data_format,
        }


class PositionsSubscription(AlorSubscription):
    """WS подписка на позиции по портфелю (W-03).

    Opcode: PositionsGetAndSubscribeV2

    Callback получает dict с полями (формат Simple):
        symbol, brokerSymbol, portfolio, exchange, avgPrice, qtyUnits,
        qtyBatch, qty, open, lotSize, dailyUnrealisedPl, unrealisedPl,
        isCurrency, existing
    """

    def __init__(
        self,
        exchange: str = 'MOEX',
        portfolio: str = '',
        skip_history: bool = False,
        data_format: str = 'Simple',
        callbacks: list[Callable[[dict], Any]] | None = None,
    ) -> None:
        super().__init__(callbacks)
        self.exchange = exchange
        self.portfolio = portfolio
        self.skip_history = skip_history
        self.data_format = data_format

    def get_subscription_message(self, **kwargs) -> dict:
        return {
            'opcode': 'PositionsGetAndSubscribeV2',
            'exchange': self.exchange,
            'portfolio': self.portfolio,
            'skipHistory': self.skip_history,
            'format': self.data_format,
        }


class SummariesSubscription(AlorSubscription):
    """WS подписка на сводную информацию по портфелю (W-04).

    Opcode: SummariesGetAndSubscribeV2

    Callback получает dict с полями (формат Simple):
        buyingPowerAtMorning, buyingPower, profit, profitRate,
        portfolioEvaluation, portfolioLiquidationValue, initialMargin,
        riskBeforeForcePositionClosing, commission
    """

    def __init__(
        self,
        exchange: str = 'MOEX',
        portfolio: str = '',
        skip_history: bool = False,
        data_format: str = 'Simple',
        callbacks: list[Callable[[dict], Any]] | None = None,
    ) -> None:
        super().__init__(callbacks)
        self.exchange = exchange
        self.portfolio = portfolio
        self.skip_history = skip_history
        self.data_format = data_format

    def get_subscription_message(self, **kwargs) -> dict:
        return {
            'opcode': 'SummariesGetAndSubscribeV2',
            'exchange': self.exchange,
            'portfolio': self.portfolio,
            'skipHistory': self.skip_history,
            'format': self.data_format,
        }
