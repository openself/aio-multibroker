import asyncio
import enum
import json
import logging
import ssl
from abc import ABC, abstractmethod
from typing import Any

import aiohttp
import websockets

from multibroker.exceptions import MBException, WebsocketClosed, WebsocketError, WebsocketReconnectionException

LOG = logging.getLogger(__name__)

CallbacksType = list[callable]


class WebsocketMgrMode(enum.Enum):
    STOPPED = enum.auto()
    RUNNING = enum.auto()
    CLOSING = enum.auto()


class Websocket(ABC):
    def __init__(self):
        pass

    @abstractmethod
    async def connect(self):
        pass

    @abstractmethod
    async def is_open(self):
        pass

    @abstractmethod
    async def close(self):
        pass

    @abstractmethod
    async def receive(self):
        pass

    @abstractmethod
    async def send(self, message: str):
        pass


class FullWebsocket(Websocket):
    def __init__(self, websocket_uri: str, builtin_ping_interval: float | None = 20,
                 max_message_size: int = 2 ** 20, ssl_context: ssl.SSLContext | None = None):
        super().__init__()

        self.websocket_uri = websocket_uri
        self.builtin_ping_interval = builtin_ping_interval
        self.max_message_size = max_message_size
        self.ssl_context = ssl_context

        self.ws = None

    async def connect(self):
        if self.ws is not None:
            raise MBException("Websocket reattempted to make connection while previous one is still active.")

        LOG.debug(f"Connecting to websocket {self.websocket_uri}")
        self.ws = await websockets.connect(self.websocket_uri,
                                           ping_interval=self.builtin_ping_interval,
                                           max_size=self.max_message_size,
                                           ssl=self.ssl_context)

    async def is_open(self):
        return self.ws is not None

    async def close(self):
        if self.ws is None:
            raise MBException("Websocket attempted to close connection while connection not open.")

        await self.ws.close()
        self.ws = None

    async def receive(self):
        if self.ws is None:
            raise MBException("Websocket attempted to read data while connection not open.")

        return await self.ws.recv()

    async def send(self, message: str):
        if self.ws is None:
            raise MBException("Websocket attempted to send data while connection not open.")

        return await self.ws.send(message)


class AiohttpWebsocket(Websocket):
    def __init__(self, websocket_uri: str, builtin_ping_interval: float | None = 20,
                 max_message_size: int = 2 ** 20, ssl_context: ssl.SSLContext | None = None):
        super().__init__()

        self.websocket_uri = websocket_uri
        self.builtin_ping_interval = builtin_ping_interval
        self.max_message_size = max_message_size
        self.ssl_context = ssl_context

        self.ws = None
        self.session: aiohttp.ClientSession | None = None

    async def connect(self):
        if self.ws is not None:
            raise MBException("Websocket reattempted to make connection while previous one is still active.")

        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(url=self.websocket_uri,
                                                max_msg_size=self.max_message_size,
                                                autoping=True,
                                                heartbeat=self.builtin_ping_interval,
                                                ssl=self.ssl_context)

    async def is_open(self):
        return self.ws is not None

    async def close(self):
        if self.ws is None:
            raise MBException("Websocket attempted to close connection while connection not open.")

        await self.ws.close()
        if self.session is not None:
            await self.session.close()
        self.ws = None
        self.session = None

    async def receive(self) -> str:
        if self.ws is None:
            raise MBException("Websocket attempted to read data while connection not open.")

        while True:
            message = await self.ws.receive()

            if message.type == aiohttp.WSMsgType.TEXT:
                if message.data == 'close cmd':
                    raise WebsocketClosed(f'Websocket was closed: {message.data}')
                return message.data
            elif message.type == aiohttp.WSMsgType.CLOSED:
                raise WebsocketClosed(f'Websocket was closed: {message.data}')
            elif message.type == aiohttp.WSMsgType.CLOSING:
                raise WebsocketClosed(f'Websocket is closing: {message.data}')
            elif message.type == aiohttp.WSMsgType.ERROR:
                raise WebsocketError(f'Websocket error: {message.data}')
            else:
                # BINARY, PING, PONG — skip non-text frames, continue reading
                LOG.debug(f"Skipping non-text WS frame type={message.type}")

    async def send(self, message: str):
        if self.ws is None:
            raise MBException("Websocket attempted to send data while connection not open.")

        return await self.ws.send_str(message)


class WebsocketOutboundMessage(ABC):
    @abstractmethod
    def to_json(self):
        pass


class ClientWebsocketHandle:
    def __init__(self, websocket: Websocket):
        self.websocket = websocket

    async def send(self, message: str | dict | WebsocketOutboundMessage):
        if isinstance(message, str):
            pass
        elif isinstance(message, dict):
            message = json.dumps(message)
        elif isinstance(message, WebsocketOutboundMessage):
            message = json.dumps(message.to_json())
        else:
            raise MBException("Only string or JSON serializable objects can be sent over the websocket.")

        LOG.debug(f"> {message}")
        return await self.websocket.send(message)

    async def receive(self):
        return await self.websocket.receive()


class WebsocketMessage:
    def __init__(self, subscription_id: Any, message: dict,
                 websocket: ClientWebsocketHandle | None = None) -> None:
        self.subscription_id = subscription_id
        self.message = message
        self.websocket = websocket


class Subscription(ABC):
    INTERNAL_SUBSCRIPTION_ID_SEQ = 0

    def __init__(self, callbacks: CallbacksType | None = None):
        self.callbacks = callbacks

        self.subscription_id = None
        self.internal_subscription_id = Subscription.INTERNAL_SUBSCRIPTION_ID_SEQ
        Subscription.INTERNAL_SUBSCRIPTION_ID_SEQ += 1

    @abstractmethod
    def construct_subscription_id(self) -> Any:
        pass

    @abstractmethod
    def get_subscription_message(self, **kwargs) -> dict:
        pass

    def get_internal_subscription_id(self) -> int:
        return self.internal_subscription_id

    def get_subscription_id(self) -> Any:
        if self.subscription_id is None:
            self.subscription_id = self.construct_subscription_id()
            LOG.debug(f"new subscription id constructed: {self.subscription_id}")

        return self.subscription_id

    async def initialize(self, **kwargs) -> None:
        pass

    async def process_message(self, message: WebsocketMessage) -> None:
        await self.process_callbacks(message)

    async def process_callbacks(self, message: WebsocketMessage) -> None:
        if self.callbacks is not None:
            tasks = []
            for cb in self.callbacks:
                # If message contains a websocket, then the websocket handle will be passed to the callbacks.
                if message.websocket is not None:
                    tasks.append(asyncio.create_task(cb(message.message, message.websocket)))
                else:
                    tasks.append(asyncio.create_task(cb(message.message)))

            # return_exceptions=True isolates callback failures:
            # a bug in one callback must NOT kill the entire WS connection,
            # otherwise a single ValueError in on_trade handler would drop
            # ALL subscriptions (bars, orders, positions).
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    LOG.error(f"Callback {self.callbacks[i].__name__} raised {type(result).__name__}: {result}",
                              exc_info=result)

    def __eq__(self, other):
        return self.internal_subscription_id == other.internal_subscription_id


class WebsocketMgr(ABC):
    WEBSOCKET_MGR_ID_SEQ = 0

    def __init__(self, websocket_uri: str, subscriptions: list[Subscription],
                 builtin_ping_interval: float | None = 20,
                 max_message_size: int = 2 ** 20, periodic_timeout_sec: int | None = None,
                 ssl_context=None,
                 auto_reconnect: bool = False, startup_delay_ms: int = 0) -> None:
        self.websocket_uri = websocket_uri
        self.subscriptions = subscriptions
        self.builtin_ping_interval = builtin_ping_interval
        self.max_message_size = max_message_size
        self.periodic_timeout_sec = periodic_timeout_sec
        self.ssl_context = ssl_context
        self.auto_reconnect = auto_reconnect
        self.startup_delay_ms = startup_delay_ms

        self.id = WebsocketMgr.WEBSOCKET_MGR_ID_SEQ
        WebsocketMgr.WEBSOCKET_MGR_ID_SEQ += 1

        self.websocket = None
        self.mode: WebsocketMgrMode = WebsocketMgrMode.STOPPED

    @abstractmethod
    async def _process_message(self, websocket: Websocket, response: str) -> None:
        pass

    async def _process_periodic(self, websocket: Websocket) -> None:
        pass

    def get_websocket_uri_variable_part(self):
        return ""

    def get_websocket(self) -> Websocket:
        return self.get_full_websocket()

    def get_full_websocket(self) -> Websocket:
        uri = self.websocket_uri + self.get_websocket_uri_variable_part()
        LOG.debug(f"Websocket URI: {uri}")

        return FullWebsocket(websocket_uri=uri,
                             builtin_ping_interval=self.builtin_ping_interval,
                             max_message_size=self.max_message_size,
                             ssl_context=self.ssl_context)

    def get_aiohttp_websocket(self) -> Websocket:
        uri = self.websocket_uri + self.get_websocket_uri_variable_part()
        LOG.debug(f"Websocket URI: {uri}")

        return AiohttpWebsocket(websocket_uri=uri,
                                builtin_ping_interval=self.builtin_ping_interval,
                                max_message_size=self.max_message_size,
                                ssl_context=self.ssl_context)

    async def validate_subscriptions(self, subscriptions: list[Subscription]) -> None:
        pass

    async def initialize_subscriptions(self, subscriptions: list[Subscription]) -> None:
        for subscription in subscriptions:
            await subscription.initialize()

    async def subscribe(self, new_subscriptions: list[Subscription]):
        await self.validate_subscriptions(new_subscriptions)
        await self.initialize_subscriptions(new_subscriptions)

        self.subscriptions += new_subscriptions

        await self.send_subscription_message(new_subscriptions)

    async def send_subscription_message(self, subscriptions: list[Subscription]):
        subscription_messages = []
        for subscription in subscriptions:
            subscription_messages.append(subscription.get_subscription_message())

        LOG.debug(f"> {subscription_messages}")
        await self.websocket.send(json.dumps(subscription_messages))

    async def unsubscribe(self, subscriptions: list[Subscription]):
        self.subscriptions = [s for s in self.subscriptions if s not in subscriptions]
        await self.send_unsubscription_message(subscriptions)

    async def send_unsubscription_message(self, subscriptions: list[Subscription]):
        raise MBException("The client does not support unsubscription messages.")

    async def send_authentication_message(self):
        pass

    async def main_loop(self):
        await self.send_authentication_message()
        await self.send_subscription_message(self.subscriptions)

        # start processing incoming messages
        while True:
            message = await self.websocket.receive()
            LOG.debug(f"< {message}")

            await self._process_message(self.websocket, message)

    async def periodic_loop(self):
        if self.periodic_timeout_sec is not None:
            while True:
                await self._process_periodic(self.websocket)
                await asyncio.sleep(self.periodic_timeout_sec)

    async def run(self) -> None:
        """Main run loop with structured concurrency via asyncio.TaskGroup (Python 3.11+)."""
        self.mode = WebsocketMgrMode.RUNNING

        await self.validate_subscriptions(self.subscriptions)
        await self.initialize_subscriptions(self.subscriptions)

        try:
            while True:
                LOG.debug(f"[{self.id}] Initiating websocket connection.")
                self.websocket = None

                await asyncio.sleep(self.startup_delay_ms / 1000.0)
                LOG.debug(f"[{self.id}] Websocket initiation delayed by {self.startup_delay_ms}ms.")

                self.websocket = self.get_websocket()
                await self.websocket.connect()

                try:
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(self.main_loop())
                        if self.periodic_timeout_sec is not None:
                            tg.create_task(self.periodic_loop())

                except BaseException as exc:
                    # TaskGroup wraps task exceptions into ExceptionGroup.
                    # Unwrap and classify: recoverable WS errors vs fatal.
                    recoverable = self._is_recoverable_exception(exc)

                    if recoverable and self.mode == WebsocketMgrMode.CLOSING:
                        LOG.debug(f"[{self.id}] Websocket is going to be shut down.")
                        break
                    if recoverable and self.auto_reconnect:
                        LOG.info(f"[{self.id}] Recoverable exception, reconnecting...")
                        self._print_subscriptions()
                        continue
                    raise

                finally:
                    if self.websocket is not None and await self.websocket.is_open():
                        LOG.debug(f"[{self.id}] Closing websocket connection.")
                        await self.websocket.close()

        except asyncio.CancelledError:
            LOG.warning(f"[{self.id}] The websocket was requested to be cancelled.")
        except Exception as e:
            LOG.error(f"[{self.id}] An exception [{e}] occurred. The websocket manager will be closed.")
            self._print_subscriptions()
            raise

    async def publish_message(self, message: WebsocketMessage) -> None:
        for subscription in self.subscriptions:
            if subscription.get_subscription_id() == message.subscription_id:
                await subscription.process_message(message)
                return

        # Orphaned message — no matching subscription. This can happen after
        # reconnect if Alor sends data on a stale guid. Using ERROR level
        # because for trading subscriptions (orders/trades) a lost message
        # means potential position desync.
        LOG.error(f"[{self.id}] Orphaned WS message: subscription_id={message.subscription_id} "
                  f"not found among {len(self.subscriptions)} active subscriptions. "
                  f"Data dropped: {str(message.message)[:200]}")

    # Recoverable WS exception types — used by run() to decide reconnect vs fatal.
    _RECOVERABLE_WS_EXCEPTIONS = (
        websockets.ConnectionClosedError,
        websockets.ConnectionClosedOK,
        websockets.exceptions.InvalidStatus,
        ConnectionResetError,
        WebsocketClosed,
        WebsocketError,
        WebsocketReconnectionException,
    )

    @classmethod
    def _is_recoverable_exception(cls, exc: BaseException) -> bool:
        """Check if *exc* (possibly an ExceptionGroup from TaskGroup) contains only recoverable WS errors."""
        if isinstance(exc, ExceptionGroup):
            return all(cls._is_recoverable_exception(e) for e in exc.exceptions)
        return isinstance(exc, cls._RECOVERABLE_WS_EXCEPTIONS)

    def _print_subscriptions(self):
        subscription_messages = []
        for subscription in self.subscriptions:
            subscription_messages.append(subscription.get_subscription_id())
        LOG.info(f"[{self.id}] Subscriptions: {subscription_messages}")

    async def shutdown(self):
        if self.mode == WebsocketMgrMode.CLOSING:
            LOG.debug(f"[{self.id}] Websocket manager already being shut down.")
            return

        self.mode = WebsocketMgrMode.CLOSING
        if self.websocket is not None:
            LOG.debug(f"[{self.id}] Manually closing websocket connection.")
            if await self.websocket.is_open():
                await self.websocket.close()
