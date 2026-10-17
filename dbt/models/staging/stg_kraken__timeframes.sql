select
    timeframe_id,
    code as timeframe_code,
    duration_seconds
from {{ source('kraken', 'timeframe') }}
