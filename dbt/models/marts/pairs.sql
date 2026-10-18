-- Pairs the report covers: tracked (top by Kraken turnover, see `crypto pairs sync`) and with at least
-- var('min_turnover_usd') of 15m turnover in the reporting window, ranked by that turnover.
with recent as (
    select pair_id, candle_time_utc, turnover_usd
    from {{ ref('stg_kraken__candles') }}
    where timeframe_code = '15m'
      and candle_time >= {{ as_of() }} - interval '{{ var("reporting_window_days") }} days'
)

select
    p.pair_id,
    p.symbol,
    min(r.candle_time_utc) as first_candle_utc,
    max(r.candle_time_utc) as last_candle_utc,
    count(*) as candles,
    sum(r.turnover_usd) as turnover_usd,
    rank() over (order by sum(r.turnover_usd) desc) as turnover_rank
from recent as r
join {{ ref('stg_kraken__pairs') }} as p using (pair_id)
where p.tracked
group by p.pair_id, p.symbol
having sum(r.turnover_usd) >= {{ var("min_turnover_usd") }}
