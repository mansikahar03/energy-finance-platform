-- Question: How accurate were the selected forecast methods in the backtest, and how much
--           less certain is the price forecast than the production forecast?
--
-- MAPE = average % miss. P90 = 90% of backtest misses were smaller than this.
-- Uses the most recent forecast run and only the methods selected for the forecast.

SELECT
    horizon_months                                                        AS months_ahead,
    round(max(mape_pct)          FILTER (WHERE series = 'production'), 1) AS production_mape_pct,
    round(max(mape_pct)          FILTER (WHERE series = 'price'), 1)      AS price_mape_pct,
    round(max(p90_abs_pct_error) FILTER (WHERE series = 'production'), 1) AS production_p90_pct,
    round(max(p90_abs_pct_error) FILTER (WHERE series = 'price'), 1)      AS price_p90_pct,
    round(max(mape_pct) FILTER (WHERE series = 'price')
          / max(mape_pct) FILTER (WHERE series = 'production'), 1)        AS price_error_multiple
FROM backtest_results
WHERE selected_for_forecast
  AND forecast_origin = (SELECT max(forecast_origin) FROM forecast_runs)
GROUP BY horizon_months
ORDER BY horizon_months;
