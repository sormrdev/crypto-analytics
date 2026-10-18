"""ORM mapping of the existing `kraken_spot_analysis` schema (Power BI reads these tables directly)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    false,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Pair(Base):
    __tablename__ = "pair"

    pair_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), unique=True)
    # On the whitelist of most traded pairs: `ohlc sync` fetches these, the report shows only these.
    tracked: Mapped[bool] = mapped_column(Boolean, server_default=false())


class TimeframeRow(Base):
    __tablename__ = "timeframe"

    timeframe_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, unique=True)


class OhlcCandle(Base):
    __tablename__ = "ohlc_candle"
    __table_args__ = (
        CheckConstraint("high_price >= open_price AND high_price >= close_price AND high_price >= low_price",
                        name="chk_high_price"),
        CheckConstraint("low_price <= open_price AND low_price <= close_price AND low_price <= high_price",
                        name="chk_low_price"),
        CheckConstraint("volume >= 0", name="chk_volume_positive"),
        CheckConstraint("candle_time >= '2013-01-01'", name="chk_candle_time_plausible"),
        Index("idx_ohlc_pair_time", "pair_id", "candle_time"),
        Index("idx_ohlc_timeframe_time", "timeframe_id", "candle_time"),
    )

    pair_id: Mapped[int] = mapped_column(ForeignKey("pair.pair_id"), primary_key=True)
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframe.timeframe_id"), primary_key=True)
    candle_time: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    open_price: Mapped[Decimal] = mapped_column(Numeric)
    high_price: Mapped[Decimal] = mapped_column(Numeric)
    low_price: Mapped[Decimal] = mapped_column(Numeric)
    close_price: Mapped[Decimal] = mapped_column(Numeric)
    volume: Mapped[Decimal] = mapped_column(Numeric)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
