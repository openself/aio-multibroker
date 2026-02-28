from multibroker.exceptions import MBException


class AlorException(MBException):
    """Base for all Alor-specific errors."""


class AlorGetTokenException(AlorException):
    """refresh_token → JWT failed."""

    def __init__(self, status_code: int, response: str) -> None:
        super().__init__(f'ALOR token refresh failed: status [{status_code}], response [{response}]')
        self.status_code = status_code
        self.body = {'response': response}


# ---------------------------------------------------------------------------
# REST exceptions — дифференцированная иерархия (S-02)
# ---------------------------------------------------------------------------


class AlorRestException(AlorException):
    """Base for REST API errors. Всегда содержит status_code и body."""

    def __init__(self, status_code: int, body: dict | None) -> None:
        super().__init__(f'ALOR REST API error: status [{status_code}], response [{body}]')
        self.status_code = status_code
        self.body = body


class BrokerBadRequestError(AlorRestException):
    """HTTP 400 — неверные параметры. НЕ повторять."""


class BrokerAuthError(AlorRestException):
    """HTTP 401 — токен истёк. Повторить ОДИН раз после refresh."""


class BrokerForbiddenError(AlorRestException):
    """HTTP 403 — нет прав. НЕ повторять."""


class BrokerNotFoundError(AlorRestException):
    """HTTP 404 — ресурс не найден. НЕ повторять."""


class BrokerRateLimitError(AlorRestException):
    """HTTP 429 — rate limit. Повторить после Retry-After или backoff."""

    def __init__(self, status_code: int, body: dict | None, retry_after_sec: float | None = None) -> None:
        super().__init__(status_code, body)
        self.retry_after_sec = retry_after_sec


class BrokerServerError(AlorRestException):
    """HTTP 500/502/503/504 — серверная ошибка. Повторить до MAX_RETRIES."""


class BrokerTimeoutError(AlorException):
    """asyncio.timeout() сработал. Повторить до MAX_RETRIES."""
