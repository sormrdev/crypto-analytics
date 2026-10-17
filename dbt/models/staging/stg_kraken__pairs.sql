select
    pair_id,
    symbol
from {{ source('kraken', 'pair') }}
