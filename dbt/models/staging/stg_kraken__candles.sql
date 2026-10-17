select
    c.pair_id,
    c.timeframe_id,
    t.timeframe_code,
    c.candle_time,
    -- Naive UTC timestamp: Power BI would otherwise shift timestamptz values to the machine's zone.
    c.candle_time at time zone 'UTC' as candle_time_utc,
    c.open_price,
    c.high_price,
    c.low_price,
    c.close_price,
    c.volume,
    c.vwap,
    c.volume * c.close_price as turnover_usd
from {{ source('kraken', 'ohlc_candle') }} as c
join {{ ref('stg_kraken__timeframes') }} as t using (timeframe_id)
