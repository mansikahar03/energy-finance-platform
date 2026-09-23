-- Question: How much did U.S. crude production change versus the same month a year earlier?
--
-- Why per day: months have 28 to 31 days, so comparing thousand barrels PER DAY
-- keeps a short February from looking like a production drop.
-- Source table: eia_actuals_monthly (validated EIA actuals).

SELECT
    strftime(cur.month, '%Y-%m')                                        AS month,
    cur.production_thousand_bbl_per_day                                 AS production_kbd,
    prev.production_thousand_bbl_per_day                                AS production_kbd_year_earlier,
    cur.production_thousand_bbl_per_day
        - prev.production_thousand_bbl_per_day                          AS change_kbd,
    round(100 * (cur.production_thousand_bbl_per_day
                 / prev.production_thousand_bbl_per_day - 1), 1)       AS change_pct
FROM eia_actuals_monthly AS cur
JOIN eia_actuals_monthly AS prev
  ON prev.month = CAST(cur.month - INTERVAL 1 YEAR AS DATE)
ORDER BY cur.month;
