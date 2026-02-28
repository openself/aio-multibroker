"""WS-подписки для live-торговли (W-01..W-04).

Все подписки наследуют AlorSubscription и используют V2 opcodes Alor API.
Формат данных — Simple (человекочитаемые имена полей).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from multibroker.clients.alor.AlorWebsocket import AlorSubscription


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
            exchange: str = "MOEX",
            portfolio: str = "",
            order_statuses: list[str] | None = None,
            skip_history: bool = False,
            data_format: str = "Simple",
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
            "opcode": "OrdersGetAndSubscribeV2",
            "exchange": self.exchange,
            "portfolio": self.portfolio,
            "skipHistory": self.skip_history,
            "format": self.data_format,
        }
        if self.order_statuses:
            msg["orderStatuses"] = self.order_statuses
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
            exchange: str = "MOEX",
            portfolio: str = "",
            skip_history: bool = False,
            data_format: str = "Simple",
            callbacks: list[Callable[[dict], Any]] | None = None,
    ) -> None:
        super().__init__(callbacks)
        self.exchange = exchange
        self.portfolio = portfolio
        self.skip_history = skip_history
        self.data_format = data_format

    def get_subscription_message(self, **kwargs) -> dict:
        return {
            "opcode": "TradesGetAndSubscribeV2",
            "exchange": self.exchange,
            "portfolio": self.portfolio,
            "skipHistory": self.skip_history,
            "format": self.data_format,
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
            exchange: str = "MOEX",
            portfolio: str = "",
            skip_history: bool = False,
            data_format: str = "Simple",
            callbacks: list[Callable[[dict], Any]] | None = None,
    ) -> None:
        super().__init__(callbacks)
        self.exchange = exchange
        self.portfolio = portfolio
        self.skip_history = skip_history
        self.data_format = data_format

    def get_subscription_message(self, **kwargs) -> dict:
        return {
            "opcode": "PositionsGetAndSubscribeV2",
            "exchange": self.exchange,
            "portfolio": self.portfolio,
            "skipHistory": self.skip_history,
            "format": self.data_format,
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
            exchange: str = "MOEX",
            portfolio: str = "",
            skip_history: bool = False,
            data_format: str = "Simple",
            callbacks: list[Callable[[dict], Any]] | None = None,
    ) -> None:
        super().__init__(callbacks)
        self.exchange = exchange
        self.portfolio = portfolio
        self.skip_history = skip_history
        self.data_format = data_format

    def get_subscription_message(self, **kwargs) -> dict:
        return {
            "opcode": "SummariesGetAndSubscribeV2",
            "exchange": self.exchange,
            "portfolio": self.portfolio,
            "skipHistory": self.skip_history,
            "format": self.data_format,
        }
