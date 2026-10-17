{{ config(materialized='table') }}

-- Holes of at least var('min_gap_candles') missing 15m candles, one row per hole.
-- A pair whose data stops before now gets an open gap up to the current candle (is_open = true).
with candles as (
    select
        pair_id,
        candle_time_utc,
        lead(candle_time_utc) over (partition by pair_id order by candle_time_utc) as next_candle_utc
    from {{ ref('candles_15m') }}
),

current_candle as (
    select date_bin('15 minutes', {{ as_of() }} at time zone 'UTC', timestamp '2000-01-01') as candle_utc
),

holes as (
    select
        c.pair_id,
        c.candle_time_utc as gap_start_utc,
        coalesce(c.next_candle_utc, cur.candle_utc) as gap_end_utc,
        c.next_candle_utc is null as is_open
    from candles as c
    cross join current_candle as cur
)

select
    pair_id,
    gap_start_utc,
    gap_end_utc,
    -- Candles strictly between the last one before the hole and the first one after it.
    (extract(epoch from gap_end_utc - gap_start_utc) / 900)::int - 1 as missing_candles,
    round(((extract(epoch from gap_end_utc - gap_start_utc) / 900)::int - 1) / 4.0, 2) as missing_hours,
    is_open
from holes
where (extract(epoch from gap_end_utc - gap_start_utc) / 900)::int - 1 >= {{ var("min_gap_candles") }}
