from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

TZ_MSK = ZoneInfo('Europe/Moscow')


def msk_datetime_to_utc_timestamp(dt: datetime) -> int:
    """Перевод московского времени в кол-во секунд, прошедших с 01.01.1970 00:00 UTC.

    :param dt: Московское время (naive → считается MSK)
    :return: UNIX timestamp (секунды)
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_MSK)
    return int(dt.timestamp())


def utc_timestamp_to_msk_datetime(seconds: int) -> datetime:
    """Перевод UNIX timestamp в московское время (naive).

    :param seconds: UNIX timestamp (секунды)
    :return: Московское время без tzinfo
    """
    dt_utc = datetime.fromtimestamp(seconds, tz=UTC)
    return dt_utc.astimezone(TZ_MSK).replace(tzinfo=None)


def msk_to_utc_datetime(dt: datetime, *, tzinfo: bool = False) -> datetime:
    """Перевод времени из московского в UTC.

    :param dt: Московское время (naive → считается MSK)
    :param tzinfo: Если True — сохранить tzinfo в результате
    :return: Время UTC
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_MSK)
    dt_utc = dt.astimezone(UTC)
    return dt_utc if tzinfo else dt_utc.replace(tzinfo=None)


def utc_to_msk_datetime(dt: datetime, *, tzinfo: bool = False) -> datetime:
    """Перевод времени из UTC в московское.

    :param dt: Время UTC (naive → считается UTC)
    :param tzinfo: Если True — сохранить tzinfo в результате
    :return: Московское время
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt_msk = dt.astimezone(TZ_MSK)
    return dt_msk if tzinfo else dt_msk.replace(tzinfo=None)


def get_request_id() -> str:
    """UUID4 — гарантированная уникальность для Alor X-REQID."""
    return str(uuid4())
