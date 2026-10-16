"""Integration tests against a real PostgreSQL (CI provides one). Each test runs inside a transaction
that is rolled back, so they never leave data behind. Skipped when no database is reachable."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from crypto_analytics.config import get_settings
from crypto_analytics.db import repository as repo
from crypto_analytics.domain import Candle, Timeframe

T0 = datetime(2013, 1, 1, tzinfo=UTC)
TF = Timeframe.M15


@pytest.fixture
def session():
    engine = create_engine(get_settings().database_url)
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("PostgreSQL not available")
    transaction = connection.begin()
    yield Session(bind=connection)
    transaction.rollback()
    connection.close()


@pytest.fixture
def pair_id(session) -> int:
    repo.add_missing_pairs(session, ["TESTPAIRUSD"])
    return repo.pair_ids(session, ["TESTPAIRUSD"])["TESTPAIRUSD"]


def candle(i: int, close: str = "1.5", volume: str = "10") -> Candle:
    return Candle(T0 + i * TF.delta, Decimal("1"), Decimal("2"), Decimal("0.5"), Decimal(close),
                  Decimal(volume), Decimal("1.2"))


def test_upsert_inserts_skips_unchanged_and_updates_changed(session, pair_id):
    assert repo.upsert_candles(session, pair_id, TF, [candle(0), candle(1)]) == 2
    assert repo.upsert_candles(session, pair_id, TF, [candle(0), candle(1)]) == 0
    assert repo.upsert_candles(session, pair_id, TF, [candle(1, close="1.9")]) == 1
    stored = session.scalar(text("SELECT close_price FROM ohlc_candle WHERE pair_id = :p AND candle_time = :t"),
                            {"p": pair_id, "t": T0 + TF.delta})
    assert stored == Decimal("1.9")


def test_upsert_handles_more_rows_than_one_batch(session, pair_id):
    rows = [candle(i) for i in range(repo.UPSERT_BATCH + 10)]
    assert repo.upsert_candles(session, pair_id, TF, rows) == len(rows)


def test_latest_candle_time_is_per_timeframe(session, pair_id):
    repo.upsert_candles(session, pair_id, TF, [candle(0), candle(5)])
    assert repo.latest_candle_time(session, pair_id, TF) == T0 + 5 * TF.delta
    assert repo.latest_candle_time(session, pair_id, Timeframe.H1) is None


def test_find_gaps_respects_min_missing(session, pair_id):
    repo.upsert_candles(session, pair_id, TF, [candle(0), candle(1), candle(3), candle(10)])
    gaps = repo.find_gaps(session, pair_id, TF, min_missing=1)
    assert [(g.after, g.before) for g in gaps] == [(T0 + TF.delta, T0 + 3 * TF.delta),
                                                   (T0 + 3 * TF.delta, T0 + 10 * TF.delta)]
    big = repo.find_gaps(session, pair_id, TF, min_missing=5)
    assert len(big) == 1 and big[0].missing == timedelta(minutes=15 * 7)


def test_check_constraints_reject_inconsistent_candles(session, pair_id):
    bad = Candle(T0, Decimal("5"), Decimal("2"), Decimal("1"), Decimal("1.5"), Decimal("1"))  # open > high
    with pytest.raises(Exception, match="chk_high_price"):
        repo.upsert_candles(session, pair_id, TF, [bad])


def test_rejects_implausible_candle_time(session, pair_id):
    epoch = Candle(datetime(1970, 1, 1, tzinfo=UTC), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"),
                   Decimal("0"))
    with pytest.raises(Exception, match="chk_candle_time_plausible"):
        repo.upsert_candles(session, pair_id, TF, [epoch])
