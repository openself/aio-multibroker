import asyncio
import datetime
import enum
import json
import logging
import socket
import ssl
import time
from abc import ABC, abstractmethod

import aiohttp
from multidict import CIMultiDictProxy

from multibroker.exceptions import MBException
from multibroker.timer import Timer
from multibroker.ws_manager import Subscription, WebsocketMgr

LOG = logging.getLogger(__name__)


class RestCallType(enum.Enum):
    GET = 'GET'
    POST = 'POST'
    DELETE = 'DELETE'
    PUT = 'PUT'


class SubscriptionSet:
    SUBSCRIPTION_SET_ID_SEQ = 0

    def __init__(self, subscriptions: list[Subscription]):
        self.subscription_set_id = SubscriptionSet.SUBSCRIPTION_SET_ID_SEQ
        SubscriptionSet.SUBSCRIPTION_SET_ID_SEQ += 1

        self.subscriptions: list[Subscription] = subscriptions
        self.websocket_mgr: WebsocketMgr | None = None

    def find_subscription(self, subscription: Subscription) -> Subscription | None:
        for s in self.subscriptions:
            if s.internal_subscription_id == subscription.internal_subscription_id:
                return s
        return None


class MBClient(ABC):
    def __init__(self, api_trace_log: bool = False, ssl_context: ssl.SSLContext | None = None) -> None:
        self.api_trace_log = api_trace_log

        self.rest_session = None
        self.subscription_sets: dict[int, SubscriptionSet] = {}

        if ssl_context is not None:
            self.ssl_context = ssl_context
        else:
            self.ssl_context = ssl.create_default_context()

    @abstractmethod
    def _get_rest_api_uri(self) -> str:
        pass

    @abstractmethod
    async def _sign_payload(
        self,
        rest_call_type: RestCallType,
        resource: str,
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> None:
        pass

    @abstractmethod
    def _preprocess_rest_response(self, status_code: int, headers: 'CIMultiDictProxy[str]', body: dict | None) -> None:
        pass

    @abstractmethod
    def _get_websocket_mgr(
        self, subscriptions: list[Subscription], startup_delay_ms: int = 0, ssl_context=None
    ) -> WebsocketMgr:
        pass

    async def close(self) -> None:
        if self.rest_session is not None and not self.rest_session.closed:
            await self.rest_session.close()
            self.rest_session = None

    async def _create_get(
        self,
        resource: str,
        params: dict | None = None,
        headers: dict | None = None,
        signed: bool = False,
        api_variable_path: str | None = None,
    ) -> dict:
        return await self._create_rest_call(
            RestCallType.GET, resource, None, params, headers, signed, api_variable_path
        )

    async def _create_post(
        self,
        resource: str,
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        signed: bool = False,
        api_variable_path: str | None = None,
    ) -> dict:
        return await self._create_rest_call(
            RestCallType.POST, resource, data, params, headers, signed, api_variable_path
        )

    async def _create_delete(
        self,
        resource: str,
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        signed: bool = False,
        api_variable_path: str | None = None,
    ) -> dict:
        return await self._create_rest_call(
            RestCallType.DELETE, resource, data, params, headers, signed, api_variable_path
        )

    async def _create_put(
        self,
        resource: str,
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        signed: bool = False,
        api_variable_path: str | None = None,
    ) -> dict:
        return await self._create_rest_call(
            RestCallType.PUT, resource, data, params, headers, signed, api_variable_path
        )

    async def _create_rest_call(
        self,
        rest_call_type: RestCallType,
        resource: str,
        data: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        signed: bool = False,
        api_variable_path: str | None = None,
    ) -> dict:
        with Timer('RestCall'):
            if headers is None:
                headers = {}
            if params is None:
                params = {}

            if signed:
                await self._sign_payload(rest_call_type, resource, data, params, headers)

            resource_uri = self._get_rest_api_uri()
            if api_variable_path is not None:
                resource_uri += api_variable_path
            resource_uri += resource

            if rest_call_type == RestCallType.GET:
                rest_call = self._get_rest_session().get(
                    resource_uri, json=data, params=params, headers=headers, ssl=self.ssl_context
                )
            elif rest_call_type == RestCallType.POST:
                rest_call = self._get_rest_session().post(
                    resource_uri, json=data, params=params, headers=headers, ssl=self.ssl_context
                )
            elif rest_call_type == RestCallType.DELETE:
                rest_call = self._get_rest_session().delete(
                    resource_uri, json=data, params=params, headers=headers, ssl=self.ssl_context
                )
            elif rest_call_type == RestCallType.PUT:
                rest_call = self._get_rest_session().put(
                    resource_uri, json=data, params=params, headers=headers, ssl=self.ssl_context
                )
            else:
                raise Exception(f'Unsupported REST call type {rest_call_type}.')

            LOG.debug(
                f'> rest type [{rest_call_type.name}], uri [{resource_uri}], '
                f'params [{params}], headers [{headers}], data [{data}]'
            )
            async with rest_call as response:
                status_code = response.status
                resp_headers = response.headers
                body = await response.text()

                LOG.debug(f'<: status [{status_code}], response [{body}]')

                if len(body) > 0:
                    try:
                        body = json.loads(body)
                    except json.JSONDecodeError:
                        body = {'raw': body}

                self._preprocess_rest_response(status_code, resp_headers, body)

                return {'status_code': status_code, 'headers': resp_headers, 'response': body}

    def _get_rest_session(self) -> aiohttp.ClientSession:
        if self.rest_session is not None and not self.rest_session.closed:
            return self.rest_session

        # TCPConnector with aggressive cleanup of dead connections:
        # - keepalive_timeout=30: close idle connections before Alor/nginx does (~60s)
        # - limit=30: bound the connection pool (we don't need 100 parallel conns)
        # - family=socket.AF_INET: force IPv4 only (fixes Docker IPv6 issues)
        connector = aiohttp.TCPConnector(
            limit=30,
            keepalive_timeout=30,
            ssl=self.ssl_context,
            family=socket.AF_INET,
        )

        if self.api_trace_log:
            trace_config = aiohttp.TraceConfig()
            trace_config.on_request_start.append(MBClient._on_request_start)
            trace_config.on_request_end.append(MBClient._on_request_end)
            trace_configs = [trace_config]
        else:
            trace_configs = None

        # timeout: 10s connect, 30s total read (per-request guard independent of
        # the asyncio.timeout in AlorClient retry wrapper)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)

        self.rest_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trace_configs=trace_configs,
        )

        return self.rest_session

    async def _recreate_rest_session(self) -> None:
        """Force-close and recreate the REST session.

        Call this when the connection pool is suspected to be in a bad state
        (e.g. after repeated aiohttp.ClientConnectionError).
        """
        if self.rest_session is not None and not self.rest_session.closed:
            LOG.warning('Force-closing REST session to recover from connection pool issues')
            await self.rest_session.close()
        self.rest_session = None

    @staticmethod
    def _clean_request_params(params: dict) -> dict:
        clean_params = {}
        for key, value in params.items():
            if value is not None:
                clean_params[key] = str(value)
        return clean_params

    @staticmethod
    async def _on_request_start(session, trace_config_ctx, params) -> None:
        LOG.debug(f'> Context: {trace_config_ctx}')
        LOG.debug(f'> Params: {params}')

    @staticmethod
    async def _on_request_end(session, trace_config_ctx, params) -> None:
        LOG.debug(f'< Context: {trace_config_ctx}')
        LOG.debug(f'< Params: {params}')

    @staticmethod
    def _get_current_timestamp_ms() -> int:
        return int(datetime.datetime.now(tz=datetime.UTC).timestamp() * 1000)

    @staticmethod
    def _get_unix_timestamp_ns() -> int:
        return time.time_ns()

    def compose_subscriptions(self, subscriptions: list[Subscription]) -> int:
        subscription_set = SubscriptionSet(subscriptions=subscriptions)
        self.subscription_sets[subscription_set.subscription_set_id] = subscription_set
        return subscription_set.subscription_set_id

    async def add_subscriptions(self, subscription_set_id: int, subscriptions: list[Subscription]) -> None:
        await self.subscription_sets[subscription_set_id].websocket_mgr.subscribe(subscriptions)

    async def unsubscribe_subscriptions(self, subscriptions: list[Subscription]) -> None:
        for subscription in subscriptions:
            subscription_found = False
            for subscription_set in self.subscription_sets.values():
                if subscription_set.find_subscription(subscription) is not None:
                    subscription_found = True
                    await subscription_set.websocket_mgr.unsubscribe(subscriptions)

            if not subscription_found:
                raise MBException(f'No active subscription {subscription.subscription_id} found.')

    async def unsubscribe_subscription_set(self, subscription_set_id: int) -> None:
        return await self.unsubscribe_subscriptions(self.subscription_sets[subscription_set_id].subscriptions)

    async def unsubscribe_all(self) -> None:
        for set_id in self.subscription_sets:
            await self.unsubscribe_subscription_set(set_id)

    async def start_websockets(self, websocket_start_time_interval_ms: int = 0) -> None:
        if len(self.subscription_sets) < 1:
            raise MBException('ERROR: There are no subscriptions to be started.')

        tasks = []
        startup_delay_ms = 0
        for subscription_set in self.subscription_sets.values():
            subscription_set.websocket_mgr = self._get_websocket_mgr(
                subscription_set.subscriptions, startup_delay_ms, self.ssl_context
            )
            tasks.append(asyncio.create_task(subscription_set.websocket_mgr.run()))
            startup_delay_ms += websocket_start_time_interval_ms

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            try:
                task.result()
            except Exception as e:
                LOG.error(f'Unrecoverable exception occurred while processing messages: {e}')
                LOG.info('Remaining websocket managers scheduled for shutdown.')

                await self.shutdown_websockets()

                if len(pending) > 0:
                    await asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED)

                LOG.info('All websocket managers shut down.')
                raise

    async def shutdown_websockets(self):
        for subscription_set in self.subscription_sets.values():
            if subscription_set.websocket_mgr is not None:
                await subscription_set.websocket_mgr.shutdown()
