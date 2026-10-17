-- Queries behind the "Key findings" section of the README.
-- Run against the pipeline database, e.g. `psql "$DATABASE_URL" -f analysis/findings.sql`.
-- All of them read reporting.candles_15m, the same view the Power BI report uses.
--
-- Analysis window: XBTUSD has continuous 15m data from 2026-01-01 to 2026-08-16; the other pairs
-- from 2026-06-06, with a collection gap 2026-06-27 -> 2026-07-19 (see query 1).


-- 1. Coverage: candles per month and pair count. Holes show up as missing/low months;
--    `crypto gaps <SYMBOL>` lists them exactly.
SELECT date_trunc('month', candle_time_utc) AS month,
       count(DISTINCT pair_id)              AS pairs,
       count(*)                             AS candles
FROM reporting.candles_15m
GROUP BY 1
ORDER BY 1;


-- 2. Session profile of XBTUSD: share of USD turnover, mean absolute 15m return (volatility)
--    and mean deviation of the close from that day's average close, by UTC hour.
WITH c AS (
    SELECT candle_time_utc AS t,
           close_price,
           volume * close_price AS usd,
           avg(close_price) OVER (PARTITION BY candle_time_utc::date) AS day_mean,
           ln(close_price / lag(close_price) OVER (ORDER BY candle_time_utc)) AS r
    FROM reporting.candles_15m
    WHERE symbol = 'XBTUSD' AND candle_time_utc < '2026-08-16'
)
SELECT extract(hour FROM t)                                       AS hour_utc,
       round(100 * sum(usd) / sum(sum(usd)) OVER (), 1)           AS turnover_share_pct,
       round((100 * avg(abs(r)))::numeric, 3)                     AS mean_abs_return_pct,
       round(100 * avg((close_price - day_mean) / day_mean), 3)   AS dev_from_day_mean_pct
FROM c
GROUP BY 1
ORDER BY 1;


-- 3. Is the time-of-day price shape real? Mean return per UTC hour with a t-statistic.
--    No hour reaches |t| >= 2; the shape in query 2 is mostly the overall downtrend
--    (mean daily log return -0.15%, XBT 87.6k -> 63.0k).
WITH c AS (
    SELECT candle_time_utc AS t,
           ln(close_price / lag(close_price) OVER (ORDER BY candle_time_utc)) AS r
    FROM reporting.candles_15m
    WHERE symbol = 'XBTUSD' AND candle_time_utc < '2026-08-16'
),
hourly AS (
    SELECT t::date AS d, extract(hour FROM t) AS hour_utc, sum(r) AS r
    FROM c
    GROUP BY 1, 2
)
SELECT hour_utc,
       count(*)                                                    AS days,
       round((100 * avg(r))::numeric, 4)                           AS mean_return_pct,
       round((avg(r) / (stddev(r) / sqrt(count(*))))::numeric, 2)  AS t_stat
FROM hourly
GROUP BY 1
ORDER BY 1;


-- 4. Turnover concentration across the 323 pairs (2026-06-07 -> 2026-08-16).
WITH turnover AS (
    SELECT symbol, sum(volume * close_price) AS usd
    FROM reporting.candles_15m
    WHERE candle_time_utc >= '2026-06-07' AND candle_time_utc < '2026-08-16'
    GROUP BY 1
),
ranked AS (
    SELECT symbol, usd,
           row_number() OVER (ORDER BY usd DESC) AS rk,
           usd / sum(usd) OVER ()                AS share
    FROM turnover
)
SELECT count(*)                                              AS pairs,
       round(sum(usd) / 1e9, 2)                              AS turnover_busd,
       round(100 * sum(share) FILTER (WHERE rk = 1), 1)      AS top1_pct,
       round(100 * sum(share) FILTER (WHERE rk <= 10), 1)    AS top10_pct,
       round(100 * sum(share) FILTER (WHERE symbol IN
           ('USDTUSD', 'USDCUSD', 'EURUSD', 'GBPUSD', 'PAXGUSD')), 1) AS stablecoin_fiat_gold_pct
FROM ranked;


-- 5. Liquidation risk of a 24h position opened at any 15m candle, by leverage.
--    Same rule as the Power BI simulator: isolated margin, 1% maintenance margin,
--    liquidated if any low (long) / high (short) inside the window crosses the liquidation price.
--    Only entries followed by 24h of gap-free data are counted.
WITH c AS (
    SELECT symbol, candle_time_utc AS t, open_price AS o,
           min(low_price)  OVER w AS lo,
           max(high_price) OVER w AS hi,
           lead(candle_time_utc, 95) OVER (PARTITION BY symbol ORDER BY candle_time_utc) AS t_last
    FROM reporting.candles_15m
    WHERE symbol IN ('XBTUSD', 'ETHUSD', 'SOLUSD') AND candle_time_utc < '2026-08-16'
    WINDOW w AS (PARTITION BY symbol ORDER BY candle_time_utc ROWS BETWEEN CURRENT ROW AND 95 FOLLOWING)
),
entries AS (
    SELECT * FROM c WHERE t_last = t + interval '23 hours 45 minutes'
)
SELECT symbol, lev AS leverage, count(*) AS entries,
       round(100.0 * avg((lo <= o * (1 - 1.0 / lev + 0.01))::int), 1) AS long_liquidated_pct,
       round(100.0 * avg((hi >= o * (1 + 1.0 / lev - 0.01))::int), 1) AS short_liquidated_pct
FROM entries
CROSS JOIN unnest(ARRAY[2, 5, 10, 20]) AS lev
GROUP BY 1, 2
ORDER BY 1, 2;


-- 6. Intraday strategy, out of sample: pick the best "long from hour A to hour B (UTC)" window on
--    Jan-Apr 2026 (train), then check how it did on May-Aug 2026 (test). 276 candidate windows.
WITH c AS (
    SELECT candle_time_utc::date AS d, extract(hour FROM candle_time_utc)::int AS h, open_price AS o
    FROM reporting.candles_15m
    WHERE symbol = 'XBTUSD' AND candle_time_utc < '2026-08-16' AND extract(minute FROM candle_time_utc) = 0
),
trades AS (
    SELECT a.h AS entry_h, b.h AS exit_h, ln(b.o / a.o) AS r,
           CASE WHEN a.d < '2026-05-01' THEN 'train' ELSE 'test' END AS sample
    FROM c a JOIN c b ON a.d = b.d AND b.h > a.h
),
stats AS (
    SELECT entry_h, exit_h, sample, avg(r) AS mean_r, avg((r > 0)::int) AS win_rate,
           avg(r) / (stddev(r) / sqrt(count(*))) AS t_stat
    FROM trades
    GROUP BY 1, 2, 3
)
SELECT tr.entry_h, tr.exit_h,
       round((100 * tr.mean_r)::numeric, 3)   AS train_mean_pct,
       round((100 * tr.win_rate)::numeric, 1) AS train_win_pct,
       round(tr.t_stat::numeric, 2)           AS train_t,
       round((100 * te.mean_r)::numeric, 3)   AS test_mean_pct,
       round((100 * te.win_rate)::numeric, 1) AS test_win_pct,
       round(te.t_stat::numeric, 2)           AS test_t
FROM stats tr
JOIN stats te USING (entry_h, exit_h)
WHERE tr.sample = 'train' AND te.sample = 'test'
ORDER BY tr.mean_r DESC
LIMIT 5;
