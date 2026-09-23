-- Question: Once newer EIA data arrives, how did earlier base forecasts compare with
--           what actually happened?
--
-- Returns no rows until a later EIA download covers months that an earlier forecast
-- predicted. Workflow: run eia_pipeline.py -> forecast.py -> load_database.py next month;
-- older forecast runs stay in the database, so this query can grade them.

SELECT
    strftime(f.forecast_origin, '%Y-%m')                        AS forecast_made_from,
    strftime(f.month, '%Y-%m')                                  AS month,
    f.horizon_months                                            AS months_ahead,
    f.production_thousand_bbl_per_day                           AS forecast_production_kbd,
    a.production_thousand_bbl_per_day                           AS actual_production_kbd,
    round(100 * (f.production_thousand_bbl_per_day
                 / a.production_thousand_bbl_per_day - 1), 1)   AS production_error_pct,
    f.wti_usd_per_bbl                                           AS forecast_wti,
    a.wti_usd_per_bbl                                           AS actual_wti,
    round(100 * (f.wti_usd_per_bbl / a.wti_usd_per_bbl - 1), 1) AS price_error_pct
FROM forecast_base_monthly AS f
JOIN eia_actuals_monthly   AS a USING (month)
ORDER BY f.forecast_origin, f.month;
