select
    pair_id,
    symbol,
    tracked
from {{ source('kraken', 'pair') }}
