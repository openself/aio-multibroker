# aio-multibroker

Асинхронная Python-библиотека для работы с брокерскими API. Текущая версия поддерживает [Alor OpenAPI V2](https://alor.dev/docs).

Предназначена для алготрейдинга: выставление заявок, мониторинг позиций и портфеля, подписка на рыночные данные через WebSocket — всё в одном async-пакете.

## Требования

- **Python ≥ 3.12**
- [Poetry](https://python-poetry.org/) для управления зависимостями

## Установка

```bash
git clone <repo-url> && cd aio-multibroker
poetry install
```

## Быстрый старт

### REST API — заявки и портфель

```python
import asyncio
from multibroker.clients.alor.AlorClient import AlorClient
from multibroker.clients.alor.enums import Exchange, OrderSide

async def main():
    async with AlorClient(refresh_token="<YOUR_TOKEN>") as client:
        # Портфель
        summary = await client.get_portfolio_summary(
            portfolio="D12345", exchange=Exchange.MOEX,
        )
        print(summary)

        # Лимитная заявка
        result = await client.create_limit_order(
            portfolio="D12345", exchange=Exchange.MOEX,
            symbol="SBER", side=OrderSide.BUY,
            quantity=10, price=250.0,
        )
        print(result)

asyncio.run(main())
```

### WebSocket — подписки в реальном времени

```python
import asyncio
from multibroker.clients.alor.AlorClient import AlorClient
from multibroker.clients.alor.AlorWebsocket import BarsSubscription
from multibroker.clients.alor.subscriptions import (
    OrdersSubscription, TradesSubscription, PositionsSubscription,
)

async def on_bar(data):
    print(f"Bar: {data}")

async def on_order(data):
    print(f"Order: {data['symbol']} {data['side']} status={data['status']}")

async def on_trade(data):
    print(f"Fill: {data['symbol']} {data['qty']}@{data['price']}")

async def main():
    async with AlorClient(refresh_token="<YOUR_TOKEN>") as client:
        # Подписка на 5-мин бары + ордера + сделки
        client.compose_subscriptions([
            BarsSubscription(
                symbol="SBER", exchange="MOEX", tf="300",
                seconds_from=0, callbacks=[on_bar],
            ),
            OrdersSubscription(
                exchange="MOEX", portfolio="D12345",
                callbacks=[on_order],
            ),
            TradesSubscription(
                exchange="MOEX", portfolio="D12345",
                callbacks=[on_trade],
            ),
        ])

        await client.start_websockets()

asyncio.run(main())
```

### Demo-режим

Для тестирования на виртуальном счёте передайте `is_demo=True`:

```python
client = AlorClient(refresh_token="<DEMO_TOKEN>", is_demo=True)
```

## API Reference

### REST — Информация о клиенте

| Метод | Описание |
|---|---|
| `get_portfolio_summary()` | Сводка по портфелю |
| `get_positions()` | Все позиции |
| `get_position()` | Позиция по инструменту |
| `get_trades()` | Сделки за сегодня |
| `get_trade()` | Сделки по инструменту |
| `get_forts_risk()` | Риски на срочном рынке |
| `get_risk()` | Риски по портфелю |
| `get_login_positions()` | Позиции по логину |
| `get_trades_history_v2()` | История сделок |
| `get_trades_symbol_v2()` | История сделок по тикеру |

### REST — Исторические данные

| Метод | Описание |
|---|---|
| `get_history()` | Свечи (бары) по инструменту |

### REST — Заявки

| Метод | Описание |
|---|---|
| `get_orders()` | Все заявки |
| `get_order()` | Заявка по ID |
| `create_market_order()` | Рыночная заявка |
| `create_limit_order()` | Лимитная заявка |
| `update_market_order()` | Изменение рыночной заявки |
| `update_limit_order()` | Изменение лимитной заявки |
| `delete_order()` | Снятие заявки |
| `delete_all_orders()` | Снятие всех заявок |
| `estimate_order()` | Оценка ГО перед выставлением |

### REST — Стоп-заявки

| Метод | Описание |
|---|---|
| `get_stop_orders()` | Все стоп-заявки |
| `get_stop_order()` | Стоп-заявка по ID |
| `create_stop_order()` | Рыночная стоп-заявка |
| `create_limit_stop_order()` | Лимитная стоп-заявка |

### WebSocket — Подписки

| Класс | Opcode | Описание |
|---|---|---|
| `BarsSubscription` | `BarsGetAndSubscribe` | Свечи в реальном времени |
| `OrdersSubscription` | `OrdersGetAndSubscribeV2` | Статусы заявок |
| `TradesSubscription` | `TradesGetAndSubscribeV2` | Сделки (fills) |
| `PositionsSubscription` | `PositionsGetAndSubscribeV2` | Позиции |
| `SummariesSubscription` | `SummariesGetAndSubscribeV2` | Сводка по портфелю |

## Перечисления (enums)

```python
from multibroker.clients.alor.enums import (
    Exchange,            # MOEX, SPBX
    OrderSide,           # BUY, SELL
    ExecutionPeriod,     # ONE_DAY, IMMEDIATE_OR_CANCEL, FILL_OR_KILL, GOOD_TILL_CANCELLED
    ExecutionCondition,  # MORE, LESS, MORE_OR_EQUAL, LESS_OR_EQUAL
    DataFormat,          # SIMPLE, SLIM, HEAVY
)
```

## Обработка ошибок

Иерархия исключений привязана к HTTP-кодам Alor API:

```
MBException
└── AlorException
    ├── AlorGetTokenException       # JWT refresh failed
    ├── AlorRestException           # Base REST error (status_code, body)
    │   ├── BrokerBadRequestError   # 400 — неверные параметры
    │   ├── BrokerAuthError         # 401 — токен истёк
    │   ├── BrokerForbiddenError    # 403 — нет прав
    │   ├── BrokerNotFoundError     # 404 — ресурс не найден
    │   ├── BrokerRateLimitError    # 429 — rate limit (retry_after_sec)
    │   └── BrokerServerError       # 5xx — ошибка сервера
    └── BrokerTimeoutError          # Timeout / connection failure
```

**Retry-логика** (встроена в `AlorClient`):

| Ошибка | Действие |
|---|---|
| `BrokerAuthError` (401) | Refresh JWT → 1 повтор |
| `BrokerRateLimitError` (429) | Ждать `Retry-After` → повтор |
| `BrokerServerError` (5xx) | Exponential backoff → до 3 попыток |
| `TimeoutError` | Backoff → до 3 попыток |
| `ServerDisconnectedError` | Пересоздать сессию → повтор |
| `BrokerBadRequestError` (400) | **Не повторять** — ошибка в параметрах |
| `BrokerForbiddenError` (403) | **Не повторять** |
| `BrokerNotFoundError` (404) | **Не повторять** |

## Защита от потери денег

Встроенные механизмы для production-trading:

- **Валидация ордеров** — `quantity ≤ 0`, `price ≤ 0`, пустой `symbol`/`side`/`portfolio` → мгновенный `ValueError` до сетевого вызова
- **X-REQID (UUID4)** — на всех мутирующих endpoints (create/update/delete) для идемпотентности при retry
- **JWT Lock** — `asyncio.Lock` с double-check предотвращает гонку при параллельных ордерах
- **Callback isolation** — исключение в одном WS-обработчике не убивает остальные подписки
- **Session recreation** — мёртвые TCP-соединения автоматически пересоздаются
- **WS auto-reconnect** — при обрыве соединения переподписка с новым JWT

## Разработка

```bash
# Линтер
make lint

# Автофикс + форматирование
make lint-fix
make format

# Тесты
make test          # базовый запуск
make test-v        # verbose + strict DeprecationWarning

# Полная проверка (lint + tests)
make check

# Всё: fix + format + test
make all
```

### Структура проекта

```
multibroker/
├── mb_client.py                    # Абстрактный базовый клиент (REST + WS)
├── ws_manager.py                   # WebSocket manager (TaskGroup, reconnect)
├── exceptions.py                   # Базовые исключения
├── timer.py                        # Таймер для REST-вызовов
├── periodic_checker.py             # Периодические проверки
├── mb.py                           # Фабрика MultiBroker
├── helpers.py                      # Утилиты
└── clients/
    └── alor/
        ├── AlorClient.py           # Alor REST API клиент
        ├── AlorWebsocket.py        # Alor WS manager + BarsSubscription
        ├── subscriptions.py        # WS-подписки (Orders, Trades, Positions, Summaries)
        ├── enums.py                # Exchange, OrderSide, ExecutionPeriod, ...
        ├── exceptions.py           # Alor-специфичные исключения
        └── functions.py            # Конвертация дат MSK↔UTC, get_request_id
tests/
├── test_retry_and_network.py       # Retry, network failures, session recreation
├── test_ws_edge_cases.py           # WS frames, malformed JSON, callback isolation
├── test_jwt_management.py          # Token refresh, race conditions, Lock
├── test_session_lifecycle.py       # aiohttp session creation/close/recreate
├── test_order_validation.py        # Order params validation, X-REQID presence
├── test_error_handling.py          # HTTP status → exception mapping
├── test_subscriptions.py           # WS subscription message formats
├── test_functions.py               # Date conversion, UUID generation
└── test_context_manager.py         # async with AlorClient()
```

### Зависимости

| Пакет | Назначение |
|---|---|
| `aiohttp` | HTTP-клиент и WebSocket |
| `websockets` | WebSocket (FullWebsocket fallback) |
| `PyJWT` | Декодирование JWT-токенов |
| `multidict` | CIMultiDict для заголовков |

Dev: `pytest`, `pytest-asyncio`, `aioresponses`, `ruff`

## Лицензия

Проприетарный код. Все права защищены.
