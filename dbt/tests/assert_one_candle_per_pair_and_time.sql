-- The report assumes one candle per pair and 15-minute slot; any row returned here is a duplicate.
select pair_id, candle_time_utc, count(*) as copies
from {{ ref('candles_15m') }}
group by pair_id, candle_time_utc
having count(*) > 1
