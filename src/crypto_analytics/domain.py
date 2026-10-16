"""Core value objects shared by the API client, aggregation and persistence layers."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum


class Timeframe(Enum):
    """Candle intervals. IDs mirror the rows seeded in the `timeframe` table."""

    M1 = (1, "1m", 60)
    M5 = (2, "5m", 300)
    M15 = (3, "15m", 900)
    M30 = (9, "30m", 1800)
    H1 = (4, "1h", 3600)
    H4 = (5, "4h", 14400)
    D1 = (6, "1d", 86400)

    def __init__(self, db_id: int, code: str, seconds: int) -> None:
        self.db_id = db_id
        self.code = code
        self.seconds = seconds

    @property
    def kraken_interval(self) -> int:
        """Interval in minutes, as expected by Kraken's OHLC endpoint."""
        return self.seconds // 60

    @property
    def delta(self) -> timedelta:
        return timedelta(seconds=self.seconds)

    def floor(self, moment: datetime) -> datetime:
        """Start of the candle that contains `moment` (UTC, aligned to the Unix epoch)."""
        ts = int(moment.timestamp())
        return datetime.fromtimestamp(ts - ts % self.seconds, tz=UTC)

    @classmethod
    def from_code(cls, code: str) -> "Timeframe":
        for tf in cls:
            if tf.code == code:
                return tf
        raise ValueError(f"Unknown timeframe {code!r}; expected one of {[tf.code for tf in cls]}")


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal | None = None
    trade_count: int | None = None


@dataclass(frozen=True, slots=True)
class Trade:
    time: datetime
    price: Decimal
    volume: Decimal
