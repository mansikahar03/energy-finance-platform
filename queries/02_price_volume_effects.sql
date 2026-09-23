-- Question: Why did illustrative production value change from one calendar year to the next?
--           How much came from producing more barrels, and how much from the oil price?
--
-- This is the standard price/volume variance analysis:
--   volume effect      = (barrels this year - barrels last year) x last year's price
--   price effect       = (price this year  - price last year)    x last year's barrels
--   interaction effect = change in barrels x change in price  (both moving together)
--   volume + price + interaction = total change, exactly.
--
-- "Price" is the volume-weighted average WTI price for the year
-- (total value / total barrels), so it matches the annual value exactly.
-- Only complete calendar years (12 months of data) are included.
--
-- Production value is illustrative (national barrels x WTI benchmark).
-- It is NOT company revenue or profit.

WITH annual AS (
    SELECT
        year(month)                                        AS year,
        count(*)                                           AS months,
        sum(production_bbl)                                AS barrels,
        sum(production_value_usd)                          AS value_usd,
        sum(production_value_usd) / sum(production_bbl)    AS avg_price
    FROM eia_actuals_monthly
    GROUP BY year(month)
    HAVING count(*) = 12
),
compared AS (
    SELECT
        cur.year,
        prev.barrels  AS barrels_prev, cur.barrels  AS barrels_cur,
        prev.avg_price AS price_prev,  cur.avg_price AS price_cur,
        prev.value_usd AS value_prev,  cur.value_usd AS value_cur
    FROM annual AS cur
    JOIN annual AS prev ON prev.year = cur.year - 1
)
SELECT
    year,
    round(barrels_cur / 1e6, 1)                                          AS production_million_bbl,
    round(100 * (barrels_cur / barrels_prev - 1), 1)                     AS volume_change_pct,
    round(price_cur, 2)                                                  AS avg_wti_usd_per_bbl,
    round(100 * (price_cur / price_prev - 1), 1)                         AS price_change_pct,
    round(value_cur / 1e9, 1)                                            AS value_usd_bn,
    round((value_cur - value_prev) / 1e9, 2)                             AS total_change_usd_bn,
    round((barrels_cur - barrels_prev) * price_prev / 1e9, 2)            AS volume_effect_usd_bn,
    round((price_cur - price_prev) * barrels_prev / 1e9, 2)              AS price_effect_usd_bn,
    round((barrels_cur - barrels_prev) * (price_cur - price_prev) / 1e9, 2) AS interaction_usd_bn
FROM compared
ORDER BY year;
