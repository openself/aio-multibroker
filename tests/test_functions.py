"""Unit-тесты для multibroker.clients.alor.functions (§6.1, S-03)."""

import re
from datetime import UTC, datetime

from multibroker.clients.alor.functions import (
    TZ_MSK,
    get_request_id,
    msk_datetime_to_utc_timestamp,
    msk_to_utc_datetime,
    utc_timestamp_to_msk_datetime,
    utc_to_msk_datetime,
)

_UUID4_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')


# ---------------------------------------------------------------------------
# datetime conversion round-trips
# ---------------------------------------------------------------------------


def test_msk_datetime_to_utc_timestamp_and_back():
    """MSK naive → UTC timestamp → MSK naive round-trip."""
    dt_msk = datetime(2025, 6, 15, 12, 0, 0)  # naive, treated as MSK
    ts = msk_datetime_to_utc_timestamp(dt_msk)
    dt_back = utc_timestamp_to_msk_datetime(ts)
    assert dt_back == dt_msk


def test_utc_timestamp_to_msk_datetime():
    """Known epoch → expected MSK datetime."""
    # 2025-01-01 00:00:00 UTC → 2025-01-01 03:00:00 MSK
    ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())
    result = utc_timestamp_to_msk_datetime(ts)
    assert result == datetime(2025, 1, 1, 3, 0, 0)
    assert result.tzinfo is None  # naive


def test_msk_to_utc_datetime_without_tzinfo():
    dt_msk = datetime(2025, 6, 15, 15, 0, 0)
    result = msk_to_utc_datetime(dt_msk)
    assert result.tzinfo is None
    assert result == datetime(2025, 6, 15, 12, 0, 0)


def test_msk_to_utc_datetime_with_tzinfo():
    dt_msk = datetime(2025, 6, 15, 15, 0, 0)
    result = msk_to_utc_datetime(dt_msk, tzinfo=True)
    assert result.tzinfo == UTC


def test_utc_to_msk_datetime_without_tzinfo():
    dt_utc = datetime(2025, 6, 15, 12, 0, 0)
    result = utc_to_msk_datetime(dt_utc)
    assert result.tzinfo is None
    assert result == datetime(2025, 6, 15, 15, 0, 0)


def test_utc_to_msk_datetime_with_tzinfo():
    dt_utc = datetime(2025, 6, 15, 12, 0, 0)
    result = utc_to_msk_datetime(dt_utc, tzinfo=True)
    assert result.tzinfo == TZ_MSK


# ---------------------------------------------------------------------------
# get_request_id — UUID4 (S-03)
# ---------------------------------------------------------------------------


def test_get_request_id_is_uuid4():
    rid = get_request_id()
    assert _UUID4_RE.match(rid), f'Expected UUID4 format, got: {rid}'


def test_get_request_id_no_collisions_100k():
    """100,000 вызовов — 0 коллизий (G-006)."""
    ids = {get_request_id() for _ in range(100_000)}
    assert len(ids) == 100_000
