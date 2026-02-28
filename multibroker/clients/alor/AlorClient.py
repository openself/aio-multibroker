from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import aiohttp
from jwt import decode
from multidict import CIMultiDictProxy

from multibroker.clients.alor.AlorWebsocket import AlorWebsocket
from multibroker.clients.alor.enums import (
    DataFormat,
    Exchange,
    ExecutionCondition,
    ExecutionPeriod,
    OrderSide,
)
from multibroker.clients.alor.exceptions import (
    AlorGetTokenException,
    AlorRestException,
    BrokerAuthError,
    BrokerBadRequestError,
    BrokerForbiddenError,
    BrokerNotFoundError,
    BrokerRateLimitError,
    BrokerServerError,
    BrokerTimeoutError,
)
from multibroker.clients.alor.functions import get_request_id
from multibroker.mb_client import MBClient, RestCallType
from multibroker.ws_manager import Subscription, WebsocketMgr

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# REST retry/timeout constants (S-01, S-02)
# ---------------------------------------------------------------------------
REST_TIMEOUT_SEC: float = 10.0
MAX_RETRIES: int = 3
RETRY_BACKOFF_BASE_SEC: float = 0.5
AUTH_RETRY_LIMIT: int = 1

# ---------------------------------------------------------------------------
# HTTP status → exception mapping
# ---------------------------------------------------------------------------
_STATUS_TO_EXC: dict[int, type[AlorRestException]] = {
    400: BrokerBadRequestError,
    401: BrokerAuthError,
    403: BrokerForbiddenError,
    404: BrokerNotFoundError,
    429: BrokerRateLimitError,
}


class AlorClient(MBClient):
    """Работа с Alor OpenAPI V2 https://alor.dev/docs из Python."""

    jwt_token_ttl = 60  # Время жизни токена JWT в секундах

    def __init__(
        self,
        refresh_token: str = '',
        is_demo: bool = False,
        api_trace_log: bool = False,
        ssl_context: ssl.SSLContext | None = None,
    ):
        """Инициализация.

        :param refresh_token: Токен
        :param is_demo: Режим демо торговли
        """
        super().__init__(api_trace_log, ssl_context)

        self.is_demo = is_demo
        self.oauth_server = f'https://oauth{"dev" if is_demo else ""}.alor.ru'
        self.rest_api_uri = f'https://api{"dev" if is_demo else ""}.alor.ru/'
        self.refresh_token = refresh_token
        self.jwt_token: str | None = None
        self.jwt_token_decoded: dict = {}
        self.jwt_token_issued: int = 0
        self.accounts: list[dict] = []
        self._jwt_aiohttp_session: aiohttp.ClientSession | None = None
        self._jwt_refresh_lock: asyncio.Lock = asyncio.Lock()

        # Получаем начальный JWT (sync, допустимо в __init__)
        self._refresh_jwt_sync()
        self._parse_accounts()

    # -----------------------------------------------------------------------
    # Context manager (S-04)
    # -----------------------------------------------------------------------
    async def __aenter__(self) -> AlorClient:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.shutdown_websockets()
        await self.close()

    async def close(self) -> None:
        """Close REST session and internal JWT aiohttp session."""
        await super().close()
        if self._jwt_aiohttp_session is not None and not self._jwt_aiohttp_session.closed:
            await self._jwt_aiohttp_session.close()
            self._jwt_aiohttp_session = None

    # -----------------------------------------------------------------------
    # JWT token management
    # -----------------------------------------------------------------------
    def _refresh_jwt_sync(self) -> str:
        """Synchronous JWT refresh — used only in __init__ (before event loop).

        Uses requests-free approach: simple urllib for sync context.
        """
        url = f'{self.oauth_server}/refresh?token={urllib.parse.quote(self.refresh_token)}'
        req = urllib.request.Request(url, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body_bytes = resp.read()
                if resp.status == 200:
                    data = json.loads(body_bytes.decode())
                    self.jwt_token = data['AccessToken']
                    self.jwt_token_decoded = decode(self.jwt_token, options={'verify_signature': False})
                    self.jwt_token_issued = int(datetime.now(UTC).timestamp())
                    return self.jwt_token

                # Non-200 with a valid HTTP response
                body_text = body_bytes.decode(errors='replace')
                LOG.error(f'ошибка получения JWT токена (sync): статус {resp.status}; ответ: {body_text}')
                self.jwt_token = None
                self.jwt_token_decoded = {}
                self.jwt_token_issued = 0
                raise AlorGetTokenException(status_code=resp.status, response=body_text)
        except AlorGetTokenException:
            raise
        except Exception as e:
            LOG.error(f'ошибка получения JWT токена (sync): {e}')
            self.jwt_token = None
            self.jwt_token_decoded = {}
            self.jwt_token_issued = 0
            raise AlorGetTokenException(status_code=0, response=str(e)) from e

    async def _refresh_jwt(self) -> str:
        """Async JWT refresh via aiohttp (M-04: no requests dependency)."""
        if self._jwt_aiohttp_session is None or self._jwt_aiohttp_session.closed:
            self._jwt_aiohttp_session = aiohttp.ClientSession()

        async with self._jwt_aiohttp_session.post(
            f'{self.oauth_server}/refresh',
            params={'token': self.refresh_token},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                self.jwt_token = data['AccessToken']
                self.jwt_token_decoded = decode(self.jwt_token, options={'verify_signature': False})
                self.jwt_token_issued = int(datetime.now(UTC).timestamp())
                return self.jwt_token

            body = await resp.text()
            LOG.error(f'ошибка получения JWT токена: статус {resp.status}; ответ: {body}')
            self.jwt_token = None
            self.jwt_token_decoded = {}
            self.jwt_token_issued = 0
            raise AlorGetTokenException(status_code=resp.status, response=body)

    async def _ensure_jwt_token(self) -> str:
        """Async-safe JWT token getter. Refreshes if expired.

        Uses asyncio.Lock to prevent concurrent refresh race condition:
        during forced flat, multiple coroutines may hit expired token
        simultaneously — without lock each would fire a separate HTTP call.
        """
        now = int(datetime.now(UTC).timestamp())
        if self.jwt_token and now - self.jwt_token_issued < self.jwt_token_ttl:
            return self.jwt_token

        async with self._jwt_refresh_lock:
            # Double-check after acquiring lock — another coroutine may have refreshed already
            now = int(datetime.now(UTC).timestamp())
            if self.jwt_token and now - self.jwt_token_issued < self.jwt_token_ttl:
                return self.jwt_token
            return await self._refresh_jwt()

    def _parse_accounts(self) -> None:
        """Parse portfolio info from decoded JWT."""
        if not self.jwt_token_decoded:
            return

        all_agreements = self.jwt_token_decoded['agreements'].split(' ')
        all_portfolios = self.jwt_token_decoded['portfolios'].split(' ')

        account_id = portfolio_id = 0

        for agreement in all_agreements:
            for portfolio in all_portfolios[portfolio_id : portfolio_id + 3]:
                if portfolio.startswith('D'):
                    type_ = 'securities'
                    exchanges = (Exchange.MOEX, Exchange.SPBX)
                    boards = (
                        'TQRD',
                        'TQOY',
                        'TQIF',
                        'TQBR',
                        'MTQR',
                        'TQOB',
                        'TQIR',
                        'EQRP_INFO',
                        'TQTF',
                        'FQDE',
                        'INDX',
                        'TQOD',
                        'FQBR',
                        'TQCB',
                        'TQPI',
                        'TQBD',
                    )
                elif portfolio.startswith('G'):
                    type_ = 'fx'
                    exchanges = (Exchange.MOEX,)
                    boards = ('CETS_SU', 'INDXC', 'CETS')
                elif portfolio.startswith('750'):
                    type_ = 'derivatives'
                    exchanges = (Exchange.MOEX,)
                    boards = ('SPBOPT', 'OPTCOMDTY', 'OPTSPOT', 'SPBFUT', 'OPTCURNCY', 'RFUD', 'ROPD')
                else:
                    LOG.warning(f'Не определен тип счета для договора {agreement}, портфеля {portfolio}')
                    continue

                self.accounts.append(
                    dict(
                        account_id=account_id,
                        agreement=agreement,
                        portfolio=portfolio,
                        type=type_,
                        exchanges=exchanges,
                        boards=boards,
                    )
                )

            account_id += 1
            portfolio_id += 3

    # -----------------------------------------------------------------------
    # MBClient abstract methods
    # -----------------------------------------------------------------------
    def _get_rest_api_uri(self) -> str:
        return self.rest_api_uri

    async def _sign_payload(
        self,
        rest_call_type: RestCallType,
        resource: str,
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> None:
        token = await self._ensure_jwt_token()
        headers['Content-Type'] = 'application/json'
        headers['Authorization'] = f'Bearer {token}'

    def _preprocess_rest_response(
        self,
        status_code: int,
        headers: CIMultiDictProxy[str],
        body: dict | None,
    ) -> None:
        if 200 <= status_code < 300:
            return

        exc_cls = _STATUS_TO_EXC.get(status_code)
        if exc_cls is not None:
            if exc_cls is BrokerRateLimitError:
                raw = headers.get('Retry-After')
                raise BrokerRateLimitError(
                    status_code=status_code,
                    body=body,
                    retry_after_sec=float(raw) if raw else None,
                )
            raise exc_cls(status_code=status_code, body=body)

        if status_code >= 500:
            raise BrokerServerError(status_code=status_code, body=body)

        # Catch-all для неизвестных кодов
        raise AlorRestException(status_code=status_code, body=body)

    def _get_websocket_mgr(
        self,
        subscriptions: list[Subscription],
        startup_delay_ms: int = 0,
        ssl_context=None,
    ) -> WebsocketMgr:
        return AlorWebsocket(
            subscriptions,
            ssl_context=ssl_context,
            alor_client=self,
            is_demo=self.is_demo,
        )

    # -----------------------------------------------------------------------
    # Order parameter validation — fail-fast before hitting the network
    # -----------------------------------------------------------------------
    @staticmethod
    def _validate_order_params(
        symbol: str,
        side: OrderSide | None,
        quantity: int,
        portfolio: str,
    ) -> None:
        """Pre-flight check for order parameters.

        Raises ValueError immediately instead of wasting a round-trip
        to Alor and burning through retry attempts on obviously bad input.
        """
        if not symbol:
            raise ValueError('Order symbol must not be empty')
        if side is None:
            raise ValueError('Order side must be specified (OrderSide.BUY or OrderSide.SELL)')
        if quantity <= 0:
            raise ValueError(f'Order quantity must be > 0, got {quantity}')
        if not portfolio:
            raise ValueError('Order portfolio must not be empty')

    # -----------------------------------------------------------------------
    # REST retry wrapper (S-01, S-02)
    # -----------------------------------------------------------------------
    async def _create_rest_call(
        self,
        rest_call_type: RestCallType,
        resource: str,
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        signed: bool = False,
        api_variable_path: str | None = None,
        timeout_sec: float = REST_TIMEOUT_SEC,
    ) -> dict:
        last_exc: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                async with asyncio.timeout(timeout_sec):
                    return await super()._create_rest_call(
                        rest_call_type,
                        resource,
                        data,
                        params,
                        headers,
                        signed,
                        api_variable_path,
                    )

            except BrokerAuthError:
                if attempt < AUTH_RETRY_LIMIT:
                    await self._refresh_jwt()
                    continue
                raise

            except BrokerRateLimitError as exc:
                wait = exc.retry_after_sec or (RETRY_BACKOFF_BASE_SEC * 2**attempt)
                await asyncio.sleep(wait)
                last_exc = exc
                continue

            except BrokerServerError as exc:
                wait = RETRY_BACKOFF_BASE_SEC * 2**attempt
                await asyncio.sleep(wait)
                last_exc = exc
                continue

            except TimeoutError:
                last_exc = BrokerTimeoutError(f'REST call timed out after {timeout_sec}s')
                wait = RETRY_BACKOFF_BASE_SEC * 2**attempt
                await asyncio.sleep(wait)
                continue

            except aiohttp.ClientConnectionError as exc:
                # Dead connection in pool, server disconnected, DNS failure, etc.
                # Force-recreate the session and retry with a fresh connection.
                LOG.warning(f'REST connection error (attempt {attempt + 1}/{MAX_RETRIES}): {type(exc).__name__}: {exc}')
                await self._recreate_rest_session()
                last_exc = BrokerTimeoutError(
                    f'Connection error after {MAX_RETRIES} attempts: {type(exc).__name__}: {exc}'
                )
                wait = RETRY_BACKOFF_BASE_SEC * 2**attempt
                await asyncio.sleep(wait)
                continue

            except aiohttp.ClientError as exc:
                # Catch-all for other aiohttp issues (payload errors, etc.)
                LOG.warning(f'REST client error (attempt {attempt + 1}/{MAX_RETRIES}): {type(exc).__name__}: {exc}')
                last_exc = BrokerTimeoutError(f'Client error: {type(exc).__name__}: {exc}')
                wait = RETRY_BACKOFF_BASE_SEC * 2**attempt
                await asyncio.sleep(wait)
                continue

            except (BrokerBadRequestError, BrokerForbiddenError, BrokerNotFoundError):
                raise  # не повторяем

        raise last_exc  # все попытки исчерпаны

    # =======================================================================
    # ClientInfo — Информация о клиенте
    # =======================================================================

    async def get_portfolio_summary(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о портфеле."""
        params = {'format': data_format.value}
        resource = f'md/v2/clients/{exchange.value}/{portfolio}/summary'
        return await self._create_get(resource, params=params, signed=True)

    async def get_positions(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        without_currency: bool = False,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о позициях."""
        params = {
            'withoutCurrency': str(without_currency),
            'format': data_format.value,
        }
        resource = f'md/v2/Clients/{exchange.value}/{portfolio}/positions'
        return await self._create_get(resource, params=params, signed=True)

    async def get_position(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о позициях выбранного инструмента."""
        params = {'format': data_format.value}
        resource = f'md/v2/Clients/{exchange.value}/{portfolio}/positions/{symbol}'
        return await self._create_get(resource, params=params, signed=True)

    async def get_trades(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        with_repo: bool = False,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о сделках."""
        params = {
            'format': data_format.value,
            'withRepo': str(with_repo),
        }
        resource = f'md/v2/Clients/{exchange.value}/{portfolio}/trades'
        return await self._create_get(resource, params=params, signed=True)

    async def get_trade(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о сделках по выбранному инструменту."""
        params = {'format': data_format.value}
        resource = f'md/v2/Clients/{exchange.value}/{portfolio}/{symbol}/trades'
        return await self._create_get(resource, params=params, signed=True)

    async def get_forts_risk(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о рисках на срочном рынке."""
        params = {'format': data_format.value}
        resource = f'md/v2/Clients/{exchange.value}/{portfolio}/fortsrisk'
        return await self._create_get(resource, params=params, signed=True)

    async def get_risk(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о рисках."""
        params = {'format': data_format.value}
        resource = f'md/v2/Clients/{exchange.value}/{portfolio}/risk'
        return await self._create_get(resource, params=params, signed=True)

    async def get_login_positions(
        self,
        login: str = '',
        without_currency: bool = False,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о позициях по логину."""
        params = {
            'format': data_format.value,
            'withoutCurrency': without_currency,
        }
        resource = f'md/v2/Clients/{login}/positions'
        return await self._create_get(resource, params=params, signed=True)

    async def get_trades_history_v2(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        ticker: str | None = None,
        date_from: str | None = None,
        id_from: int | None = None,
        limit: int | None = None,
        descending: bool | None = None,
        side: OrderSide | None = None,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение истории сделок v2."""
        params: dict = {'format': data_format.value}
        if ticker:
            params['ticker'] = ticker
        if date_from:
            params['dateFrom'] = date_from
        if id_from:
            params['from'] = id_from
        if limit:
            params['limit'] = limit
        if descending:
            params['descending'] = descending
        if side:
            params['side'] = side.value

        resource = f'md/v2/Stats/{exchange.value}/{portfolio}/history/trades'
        return await self._create_get(resource, params=params, signed=True)

    async def get_trades_symbol_v2(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        date_from: str | None = None,
        id_from: int | None = None,
        limit: int | None = None,
        is_descending: bool = False,
        side: OrderSide | None = None,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение истории сделок (один тикер) v2."""
        params: dict = {'format': data_format.value}
        if date_from:
            params['dateFrom'] = date_from
        if id_from:
            params['from'] = id_from
        if limit:
            params['limit'] = limit
        if is_descending:
            params['descending'] = str(is_descending)
        if side:
            params['side'] = side.value

        resource = f'md/v2/Stats/{exchange.value}/{portfolio}/history/trades/{symbol}'
        return await self._create_get(resource, params=params, signed=True)

    # =======================================================================
    # Instruments — Ценные бумаги / инструменты
    # =======================================================================

    async def get_history(
        self,
        exchange: Exchange | None = None,
        symbol: str = '',
        tf: int = 0,
        seconds_from: int = 1,
        seconds_to: int = 32536799999,
        count_back: int = 0,
        use_untraded: bool = False,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Запрос истории рынка для выбранных биржи и финансового инструмента."""
        params = {
            'exchange': exchange.value,
            'symbol': symbol,
            'tf': tf,
            'from': max(0, seconds_from - 1),
            'to': seconds_to,
            'countBack': count_back,
            'untraded': str(use_untraded),
            'format': data_format.value,
        }
        resource = 'md/v2/history'
        return await self._create_get(resource, params=params, signed=True)

    # =======================================================================
    # Orders — Биржевые заявки
    # =======================================================================

    async def get_orders(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о всех заявках."""
        params = {'format': data_format.value}
        resource = f'md/v2/clients/{exchange}/{portfolio}/orders'
        return await self._create_get(resource, params=params, signed=True)

    async def get_order(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        order_id: str = '',
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о выбранной заявке."""
        params = {'format': data_format.value}
        resource = f'md/v2/clients/{exchange}/{portfolio}/orders/{order_id}'
        return await self._create_get(resource, params=params, signed=True)

    async def create_market_order(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        symbol_group: str = '',
        side: OrderSide | None = None,
        quantity: int = 0,
        comment: str = '',
        time_in_force: ExecutionPeriod = ExecutionPeriod.GOOD_TILL_CANCELLED,
    ) -> dict:
        """Создание рыночной заявки."""
        self._validate_order_params(symbol=symbol, side=side, quantity=quantity, portfolio=portfolio)
        headers = {'X-REQID': get_request_id()}
        data = {
            'side': str(side),
            'type': 'market',
            'quantity': quantity,
            'instrument': {'symbol': symbol, 'exchange': str(exchange), 'instrumentGroup': symbol_group},
            'user': {'portfolio': portfolio},
            'comment': comment,
            'timeInForce': str(time_in_force),
        }
        resource = '/commandapi/warptrans/TRADE/v2/client/orders/actions/market'
        return await self._create_post(resource, data=data, headers=headers, signed=True)

    async def create_limit_order(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        symbol_group: str = '',
        side: OrderSide | None = None,
        quantity: int = 0,
        comment: str = '',
        time_in_force: ExecutionPeriod = ExecutionPeriod.GOOD_TILL_CANCELLED,
        price: float = 0.0,
        iceberg_fixed: int | None = None,
        iceberg_variance: int | None = None,
    ) -> dict:
        """Создание лимитной заявки."""
        self._validate_order_params(symbol=symbol, side=side, quantity=quantity, portfolio=portfolio)
        if price <= 0:
            raise ValueError(f'Limit order price must be > 0, got {price}')
        headers = {'X-REQID': get_request_id()}
        data = {
            'side': str(side),
            'type': 'limit',
            'quantity': quantity,
            'price': price,
            'instrument': {'symbol': symbol, 'exchange': str(exchange), 'instrumentGroup': symbol_group},
            'user': {'portfolio': portfolio},
            'comment': comment,
            'timeInForce': str(time_in_force),
        }
        if iceberg_fixed:
            data['icebergFixed'] = iceberg_fixed
        if iceberg_variance:
            data['icebergVariance'] = iceberg_variance

        resource = '/commandapi/warptrans/TRADE/v2/client/orders/actions/limit'
        return await self._create_post(resource, data=data, headers=headers, signed=True)

    async def delete_order(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        order_id: str = '',
        is_stop_order: bool = False,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Снятие заявки."""
        headers = {'X-REQID': get_request_id()}
        data = {
            'portfolio': portfolio,
            'exchange': str(exchange),
            'stop': str(is_stop_order),
            'format': str(data_format),
        }
        resource = f'/commandapi/warptrans/TRADE/v2/client/orders/{order_id}'
        return await self._create_delete(resource, data=data, headers=headers, signed=True)

    async def delete_all_orders(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        is_stop_order: bool | None = None,
    ) -> dict:
        """Снять все биржевые и/или условные заявки для указанного портфеля."""
        headers = {'X-REQID': get_request_id()}
        data: dict = {
            'portfolio': portfolio,
            'exchange': str(exchange),
        }
        if is_stop_order is not None:
            data['stop'] = str(is_stop_order)

        resource = '/commandapi/warptrans/TRADE/v2/client/orders/all'
        return await self._create_delete(resource, data=data, headers=headers, signed=True)

    # =======================================================================
    # Stop Orders — условные заявки
    # =======================================================================

    async def get_stop_orders(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о всех стоп-заявках."""
        params = {'format': data_format.value}
        resource = f'md/v2/clients/{exchange}/{portfolio}/stoporders'
        return await self._create_get(resource, params=params, signed=True)

    async def get_stop_order(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        order_id: str = '',
        data_format: DataFormat = DataFormat.SIMPLE,
    ) -> dict:
        """Получение информации о выбранной стоп-заявке."""
        params = {'format': data_format.value}
        resource = f'md/v2/clients/{exchange}/{portfolio}/stoporders/{order_id}'
        return await self._create_get(resource, params=params, signed=True)

    async def create_limit_stop_order(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        symbol_group: str = '',
        side: OrderSide | None = None,
        quantity: int = 0,
        time_in_force: ExecutionPeriod = ExecutionPeriod.GOOD_TILL_CANCELLED,
        price: float = 0.0,
        iceberg_fixed: int | None = None,
        iceberg_variance: int | None = None,
        condition: ExecutionCondition | None = None,
        trigger_price: float = 0.0,
        stop_end_unix_time: int = 0,
        protecting_seconds: int = 0,
        need_activate: bool = True,
    ) -> dict:
        """Создание лимитной стоп-заявки."""
        self._validate_order_params(symbol=symbol, side=side, quantity=quantity, portfolio=portfolio)
        if price <= 0:
            raise ValueError(f'Stop-limit order price must be > 0, got {price}')
        if trigger_price <= 0:
            raise ValueError(f'Trigger price must be > 0, got {trigger_price}')
        headers = {'X-REQID': get_request_id()}
        data = {
            'side': str(side),
            'type': 'limit',
            'quantity': quantity,
            'price': price,
            'instrument': {'symbol': symbol, 'exchange': str(exchange), 'instrumentGroup': symbol_group},
            'user': {'portfolio': portfolio},
            'timeInForce': str(time_in_force),
            'condition': str(condition),
            'triggerPrice': trigger_price,
            'activate': str(need_activate),
        }
        if iceberg_fixed:
            data['icebergFixed'] = iceberg_fixed
        if iceberg_variance:
            data['icebergVariance'] = iceberg_variance
        if stop_end_unix_time:
            data['stopEndUnixTime'] = stop_end_unix_time
        if protecting_seconds:
            data['protectingSeconds'] = protecting_seconds

        resource = '/commandapi/warptrans/TRADE/v2/client/orders/actions/stopLimit'
        return await self._create_post(resource, data=data, headers=headers, signed=True)

    # =======================================================================
    # Новые REST-методы (R-01..R-04)
    # =======================================================================

    async def create_stop_order(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        symbol_group: str = '',
        side: OrderSide | None = None,
        quantity: int = 0,
        condition: ExecutionCondition | None = None,
        trigger_price: float = 0.0,
        stop_end_unix_time: int = 0,
        comment: str = '',
    ) -> dict:
        """Создание рыночной стоп-заявки (R-01).

        При срабатывании условия на биржу отправляется РЫНОЧНАЯ заявка.
        Endpoint: POST /commandapi/warptrans/TRADE/v2/client/orders/actions/stop
        """
        self._validate_order_params(symbol=symbol, side=side, quantity=quantity, portfolio=portfolio)
        if trigger_price <= 0:
            raise ValueError(f'Trigger price must be > 0, got {trigger_price}')
        headers = {'X-REQID': get_request_id()}
        data = {
            'side': str(side),
            'type': 'market',
            'quantity': quantity,
            'instrument': {'symbol': symbol, 'exchange': str(exchange), 'instrumentGroup': symbol_group},
            'user': {'portfolio': portfolio},
            'condition': str(condition),
            'triggerPrice': trigger_price,
            'stopEndUnixTime': stop_end_unix_time,
            'activate': True,
            'comment': comment,
        }
        resource = '/commandapi/warptrans/TRADE/v2/client/orders/actions/stop'
        return await self._create_post(resource, data=data, headers=headers, signed=True)

    async def update_market_order(
        self,
        order_id: str,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        symbol_group: str = '',
        side: OrderSide | None = None,
        quantity: int = 0,
        comment: str = '',
        time_in_force: ExecutionPeriod = ExecutionPeriod.GOOD_TILL_CANCELLED,
    ) -> dict:
        """Изменение рыночной заявки (R-02).

        Endpoint: PUT /commandapi/warptrans/TRADE/v2/client/orders/actions/market/{orderId}
        """
        self._validate_order_params(symbol=symbol, side=side, quantity=quantity, portfolio=portfolio)
        headers = {'X-REQID': get_request_id()}
        data = {
            'side': str(side),
            'type': 'market',
            'quantity': quantity,
            'instrument': {'symbol': symbol, 'exchange': str(exchange), 'instrumentGroup': symbol_group},
            'user': {'portfolio': portfolio},
            'comment': comment,
            'timeInForce': str(time_in_force),
        }
        resource = f'/commandapi/warptrans/TRADE/v2/client/orders/actions/market/{order_id}'
        return await self._create_put(resource, data=data, headers=headers, signed=True)

    async def update_limit_order(
        self,
        order_id: str,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        symbol_group: str = '',
        side: OrderSide | None = None,
        quantity: int = 0,
        price: float = 0.0,
        comment: str = '',
        time_in_force: ExecutionPeriod = ExecutionPeriod.GOOD_TILL_CANCELLED,
    ) -> dict:
        """Изменение лимитной заявки (R-03).

        Endpoint: PUT /commandapi/warptrans/TRADE/v2/client/orders/actions/limit/{orderId}
        """
        self._validate_order_params(symbol=symbol, side=side, quantity=quantity, portfolio=portfolio)
        if price <= 0:
            raise ValueError(f'Limit order price must be > 0, got {price}')
        headers = {'X-REQID': get_request_id()}
        data = {
            'side': str(side),
            'type': 'limit',
            'quantity': quantity,
            'price': price,
            'instrument': {'symbol': symbol, 'exchange': str(exchange), 'instrumentGroup': symbol_group},
            'user': {'portfolio': portfolio},
            'comment': comment,
            'timeInForce': str(time_in_force),
        }
        resource = f'/commandapi/warptrans/TRADE/v2/client/orders/actions/limit/{order_id}'
        return await self._create_put(resource, data=data, headers=headers, signed=True)

    async def estimate_order(
        self,
        portfolio: str = '',
        exchange: Exchange | None = None,
        symbol: str = '',
        side: OrderSide | None = None,
        quantity: int = 0,
        price: float | None = None,
    ) -> dict:
        """Оценка заявки — хватит ли ГО (R-04).

        Endpoint: POST /commandapi/warptrans/TRADE/v2/client/orders/estimate
        """
        data: dict = {
            'portfolio': portfolio,
            'ticker': symbol,
            'exchange': str(exchange),
            'board': '',  # Alor определяет автоматически
            'side': str(side),
            'quantity': quantity,
        }
        if price is not None:
            data['price'] = price

        resource = '/commandapi/warptrans/TRADE/v2/client/orders/estimate'
        return await self._create_post(resource, data=data, signed=True)
