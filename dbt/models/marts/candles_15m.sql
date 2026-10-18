-- The fact table Power BI imports: 15m candles of reported pairs inside the reporting window.
select
    c.pair_id,
    c.timeframe_id,
    p.symbol,
    c.candle_time_utc,
    c.open_price,
    c.high_price,
    c.low_price,
    c.close_price,
    c.volume,
    c.vwap
from {{ ref('stg_kraken__candles') }} as c
join {{ ref('pairs') }} as p using (pair_id)
where c.timeframe_code = '15m'
  and c.candle_time >= {{ as_of() }} - interval '{{ var("reporting_window_days") }} days'
