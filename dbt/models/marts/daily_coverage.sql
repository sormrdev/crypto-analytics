{{ config(materialized='table') }}

-- One row per pair and UTC day, from the pair's first candle to today: how many 15m candles exist
-- out of those expected. Days without any candle are kept (a date spine), so holes show as 0%.
with now_utc as (
    select {{ as_of() }} at time zone 'UTC' as ts
),

first_days as (
    select pair_id, min(candle_time_utc)::date as first_day
    from {{ ref('candles_15m') }}
    group by pair_id
),

spine as (
    select f.pair_id, d::date as day
    from first_days as f
    cross join now_utc as n
    cross join lateral generate_series(f.first_day, n.ts::date, interval '1 day') as d
),

counts as (
    select pair_id, candle_time_utc::date as day, count(*) as candles
    from {{ ref('candles_15m') }}
    group by pair_id, candle_time_utc::date
)

select
    s.pair_id,
    s.day,
    coalesce(c.candles, 0) as candles,
    e.expected_candles,
    least(1.0, coalesce(c.candles, 0)::numeric / nullif(e.expected_candles, 0)) as coverage
from spine as s
cross join now_utc as n
-- Today only counts the candles that have closed so far.
cross join lateral (
    select case when s.day = n.ts::date
                then floor(extract(epoch from n.ts - s.day::timestamp) / 900)::int
                else 96 end as expected_candles
) as e
left join counts as c using (pair_id, day)
