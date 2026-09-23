-- Question: Over the next 12 months, what is the range of illustrative production value
--           across the low, base and high scenarios, and how does it compare with the
--           last 12 actual months?
--
-- Uses the most recent forecast run. Summing the "low" months assumes every month is
-- simultaneously at its low end (and likewise for "high"), so this is a stress range,
-- not a probability range.
--
-- Production value is illustrative (national barrels x WTI benchmark).
-- It is NOT company revenue or profit.

WITH latest_run AS (
    SELECT max(forecast_origin) AS forecast_origin FROM forecast_runs
),
forecast_totals AS (
    SELECT
        s.scenario,
        min(s.month)                                                  AS first_month,
        max(s.month)                                                  AS last_month,
        sum(s.production_thousand_bbl_per_day * s.days_in_month) / 1000 AS production_million_bbl,
        avg(s.wti_usd_per_bbl)                                        AS avg_wti_usd_per_bbl,
        sum(s.production_value_usd_billions)                          AS value_usd_bn
    FROM forecast_scenarios_monthly AS s
    JOIN latest_run USING (forecast_origin)
    GROUP BY s.scenario
),
last_12_actual AS (
    SELECT
        'actual (last 12 months)'                   AS scenario,
        min(month)                                  AS first_month,
        max(month)                                  AS last_month,
        sum(production_thousand_bbl) / 1000         AS production_million_bbl,
        avg(wti_usd_per_bbl)                        AS avg_wti_usd_per_bbl,
        sum(production_value_usd_billions)          AS value_usd_bn
    FROM eia_actuals_monthly
    WHERE month > (SELECT max(month) FROM eia_actuals_monthly) - INTERVAL 12 MONTH
)
SELECT
    scenario,
    strftime(first_month, '%Y-%m') || ' to ' || strftime(last_month, '%Y-%m') AS period,
    round(production_million_bbl, 1)   AS production_million_bbl,
    round(avg_wti_usd_per_bbl, 2)      AS avg_wti_usd_per_bbl,
    round(value_usd_bn, 1)             AS value_usd_bn
FROM (SELECT * FROM forecast_totals UNION ALL SELECT * FROM last_12_actual)
ORDER BY CASE scenario WHEN 'actual (last 12 months)' THEN 0 WHEN 'low' THEN 1
                       WHEN 'base' THEN 2 ELSE 3 END;
