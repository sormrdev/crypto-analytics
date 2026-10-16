from datetime import UTC, datetime, timedelta, timezone

import pytest

from crypto_analytics.domain import Timeframe


@pytest.mark.parametrize(("tf", "expected"), [
    (Timeframe.M15, datetime(2026, 6, 17, 10, 15, tzinfo=UTC)),
    (Timeframe.H1, datetime(2026, 6, 17, 10, 0, tzinfo=UTC)),
    (Timeframe.D1, datetime(2026, 6, 17, 0, 0, tzinfo=UTC)),
])
def test_floor(tf, expected):
    assert tf.floor(datetime(2026, 6, 17, 10, 29, 59, tzinfo=UTC)) == expected


def test_floor_normalises_other_timezones_to_utc():
    warsaw_summer = timezone(timedelta(hours=2))
    floored = Timeframe.M15.floor(datetime(2026, 6, 17, 12, 7, tzinfo=warsaw_summer))
    assert floored == datetime(2026, 6, 17, 10, 0, tzinfo=UTC)


def test_ids_match_timeframe_table():
    assert Timeframe.M15.db_id == 3
    assert Timeframe.M1.db_id == 1
    assert Timeframe.M15.kraken_interval == 15


def test_from_code():
    assert Timeframe.from_code("4h") is Timeframe.H4
    with pytest.raises(ValueError):
        Timeframe.from_code("2m")
