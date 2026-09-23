"""
Database loader - Milestone 5.

Copies the validated pipeline outputs into a local DuckDB database so they
can be queried with SQL. It does not recalculate anything: the CSV files from
eia_pipeline.py and forecast.py remain the source of truth.

Tables:
  eia_actuals_monthly         one row per month of validated EIA actuals
  forecast_runs               one row per forecast (identified by the last actual month it used)
  forecast_scenarios_monthly  one row per forecast month per scenario (low / base / high)
  backtest_results            one row per series, method and horizon, per forecast run
View:
  forecast_base_monthly       base-scenario forecast rows, joined to the run's methods

Safe to rerun:
  - Actuals are keyed by month. Reloading replaces a month (for example after an
    EIA revision) instead of adding a duplicate.
  - Forecasts, scenarios and backtests are keyed by forecast_origin. Reloading
    the same forecast replaces its rows; a newer forecast is added alongside
    older ones, so past forecasts can later be compared with what happened.
  - Everything happens in one transaction. If any check fails, nothing is saved.

Run from the project folder (after eia_pipeline.py and forecast.py):
    python load_database.py
"""

import csv
import sys
from decimal import Decimal
from pathlib import Path

import duckdb

PROJECT_DIR = Path(__file__).parent
DATABASE_FILE = PROJECT_DIR / "database" / "energy_finance.duckdb"
EIA_FILE = PROJECT_DIR / "output" / "eia_us_crude_production_value.csv"
FORECAST_FILE = PROJECT_DIR / "output" / "forecast_12_months.csv"
BACKTEST_FILE = PROJECT_DIR / "output" / "forecast_backtest.csv"

SCENARIOS = ("low", "base", "high")

SCHEMA = """
CREATE TABLE IF NOT EXISTS eia_actuals_monthly (
    month                            DATE PRIMARY KEY,   -- first day of the month
    production_thousand_bbl          BIGINT NOT NULL,    -- EIA MCRFPUS1, thousand barrels in the month
    production_thousand_bbl_per_day  DECIMAL(12, 3) NOT NULL,  -- EIA MCRFPUS2
    production_bbl                   BIGINT NOT NULL,    -- barrels in the month
    wti_usd_per_bbl                  DECIMAL(10, 2) NOT NULL,  -- EIA RWTC, monthly average
    production_value_usd             BIGINT NOT NULL,    -- illustrative: barrels x WTI; NOT revenue
    production_value_usd_billions    DECIMAL(12, 3) NOT NULL,
    source_file                      VARCHAR NOT NULL,
    loaded_at                        TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS forecast_runs (
    forecast_origin       DATE PRIMARY KEY,  -- last actual month the forecast was built from
    first_forecast_month  DATE NOT NULL,
    last_forecast_month   DATE NOT NULL,
    horizon_months        INTEGER NOT NULL,
    production_method     VARCHAR NOT NULL,
    price_method          VARCHAR NOT NULL,
    source_file           VARCHAR NOT NULL,
    loaded_at             TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS forecast_scenarios_monthly (
    forecast_origin                  DATE NOT NULL,
    month                            DATE NOT NULL,
    scenario                         VARCHAR NOT NULL CHECK (scenario IN ('low', 'base', 'high')),
    horizon_months                   INTEGER NOT NULL,   -- 1 = first month after the origin
    days_in_month                    INTEGER NOT NULL,
    production_thousand_bbl_per_day  DECIMAL(12, 1) NOT NULL,
    wti_usd_per_bbl                  DECIMAL(10, 2) NOT NULL,
    production_value_usd_billions    DECIMAL(12, 3) NOT NULL,  -- illustrative; NOT revenue
    PRIMARY KEY (forecast_origin, month, scenario)
);

CREATE TABLE IF NOT EXISTS backtest_results (
    forecast_origin        DATE NOT NULL,
    series                 VARCHAR NOT NULL,   -- 'production' or 'price'
    units                  VARCHAR NOT NULL,
    method                 VARCHAR NOT NULL,
    horizon_months         INTEGER NOT NULL,
    n_forecasts            INTEGER NOT NULL,
    mae                    DOUBLE NOT NULL,
    mape_pct               DOUBLE NOT NULL,
    bias_pct               DOUBLE NOT NULL,
    p90_abs_pct_error      DOUBLE NOT NULL,
    selected_for_forecast  BOOLEAN NOT NULL,
    PRIMARY KEY (forecast_origin, series, method, horizon_months)
);

CREATE OR REPLACE VIEW forecast_base_monthly AS
SELECT s.forecast_origin, s.month, s.horizon_months, s.days_in_month,
       s.production_thousand_bbl_per_day, s.wti_usd_per_bbl, s.production_value_usd_billions,
       r.production_method, r.price_method
FROM forecast_scenarios_monthly s
JOIN forecast_runs r USING (forecast_origin)
WHERE s.scenario = 'base';
"""


def month_date(text):
    """'2026-06' -> '2026-06-01' (DuckDB DATE text)."""
    return f"{text}-01"


def read_csv(path):
    if not path.exists():
        sys.exit(f"{path.relative_to(PROJECT_DIR)} not found. Run the pipelines first "
                 f"(python eia_pipeline.py, then python forecast.py).")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fail(con, message):
    con.execute("ROLLBACK")
    sys.exit(f"Load stopped, nothing was saved:\n  {message}")


# ---------------------------------------------------------------------------
# Load steps (all run inside one transaction)
# ---------------------------------------------------------------------------
def load_actuals(con, rows):
    months = [r["month"] for r in rows]
    if len(months) != len(set(months)):
        fail(con, f"{EIA_FILE.name} contains a duplicate month")
    con.executemany(
        """INSERT OR REPLACE INTO eia_actuals_monthly VALUES (?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)""",
        [(month_date(r["month"]), r["us_crude_production_thousand_bbl"],
          r["us_crude_production_thousand_bbl_per_day"], r["us_crude_production_bbl"],
          r["wti_spot_price_usd_per_bbl"], r["illustrative_production_value_usd"],
          r["illustrative_production_value_usd_billions"], EIA_FILE.name) for r in rows],
    )


def load_forecast(con, forecast_rows, backtest_rows, origin):
    future = [r for r in forecast_rows if r["record_type"] == "forecast"]
    selected = {r["series"]: r["method"] for r in backtest_rows if r["selected_for_forecast"] == "yes"}
    if set(selected) != {"production", "price"}:
        fail(con, f"{BACKTEST_FILE.name} must mark exactly one selected method for production and for price")

    # Replace this forecast run's rows so reruns never duplicate them.
    for table in ("forecast_scenarios_monthly", "backtest_results", "forecast_runs"):
        con.execute(f"DELETE FROM {table} WHERE forecast_origin = ?", [month_date(origin)])

    con.execute(
        "INSERT INTO forecast_runs VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp)",
        [month_date(origin), month_date(future[0]["month"]), month_date(future[-1]["month"]), len(future),
         selected["production"], selected["price"], FORECAST_FILE.name],
    )
    con.executemany(
        "INSERT INTO forecast_scenarios_monthly VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(month_date(origin), month_date(r["month"]), case, horizon, r["days_in_month"],
          r[f"forecast_production_thousand_bbl_per_day_{case}"], r[f"forecast_wti_usd_per_bbl_{case}"],
          r[f"forecast_production_value_usd_billions_{case}"])
         for horizon, r in enumerate(future, start=1) for case in SCENARIOS],
    )
    con.executemany(
        "INSERT INTO backtest_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(month_date(origin), r["series"], r["units"], r["method"], r["horizon_months"], r["n_forecasts"],
          r["mae"], r["mape_pct"], r["bias_pct"], r["p90_abs_pct_error"], r["selected_for_forecast"] == "yes")
         for r in backtest_rows],
    )


# ---------------------------------------------------------------------------
# Verification: compare every loaded row with the CSV files
# ---------------------------------------------------------------------------
def verify(con, eia_rows, forecast_rows, backtest_rows, origin):
    problems = []
    origin_date = month_date(origin)

    # Actuals: every CSV month is in the table with identical values.
    db = {str(r[0])[:7]: r[1:] for r in con.execute(
        """SELECT month, production_thousand_bbl, production_thousand_bbl_per_day, production_bbl,
                  wti_usd_per_bbl, production_value_usd, production_value_usd_billions
           FROM eia_actuals_monthly""").fetchall()}
    for r in eia_rows:
        expected = (int(r["us_crude_production_thousand_bbl"]), Decimal(r["us_crude_production_thousand_bbl_per_day"]),
                    int(r["us_crude_production_bbl"]), Decimal(r["wti_spot_price_usd_per_bbl"]),
                    int(r["illustrative_production_value_usd"]),
                    Decimal(r["illustrative_production_value_usd_billions"]))
        if db.get(r["month"]) != expected:
            problems.append(f"eia_actuals_monthly {r['month']}: database {db.get(r['month'])} != CSV {expected}")

    # Scenarios: 3 rows per forecast month, each matching the CSV.
    future = [r for r in forecast_rows if r["record_type"] == "forecast"]
    db = {(str(m)[:7], s): (p, w, v) for m, s, p, w, v in con.execute(
        """SELECT month, scenario, production_thousand_bbl_per_day, wti_usd_per_bbl, production_value_usd_billions
           FROM forecast_scenarios_monthly WHERE forecast_origin = ?""", [origin_date]).fetchall()}
    if len(db) != len(future) * len(SCENARIOS):
        problems.append(f"forecast_scenarios_monthly has {len(db)} rows for {origin}, "
                        f"expected {len(future) * len(SCENARIOS)}")
    for r in future:
        for case in SCENARIOS:
            expected = (Decimal(r[f"forecast_production_thousand_bbl_per_day_{case}"]),
                        Decimal(r[f"forecast_wti_usd_per_bbl_{case}"]),
                        Decimal(r[f"forecast_production_value_usd_billions_{case}"]))
            if db.get((r["month"], case)) != expected:
                problems.append(f"forecast_scenarios_monthly {r['month']} {case}: "
                                f"database {db.get((r['month'], case))} != CSV {expected}")

    # Backtest: same row count and identical error figures.
    db = {(s, m, h): (mape, p90) for s, m, h, mape, p90 in con.execute(
        """SELECT series, method, horizon_months, mape_pct, p90_abs_pct_error
           FROM backtest_results WHERE forecast_origin = ?""", [origin_date]).fetchall()}
    if len(db) != len(backtest_rows):
        problems.append(f"backtest_results has {len(db)} rows for {origin}, expected {len(backtest_rows)}")
    for r in backtest_rows:
        key = (r["series"], r["method"], int(r["horizon_months"]))
        if db.get(key) != (float(r["mape_pct"]), float(r["p90_abs_pct_error"])):
            problems.append(f"backtest_results {key}: database {db.get(key)} != CSV")

    # No duplicates anywhere (primary keys should guarantee this; check anyway).
    for table, key in (("eia_actuals_monthly", "month"),
                       ("forecast_scenarios_monthly", "forecast_origin, month, scenario"),
                       ("backtest_results", "forecast_origin, series, method, horizon_months")):
        dupes = con.execute(f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY ALL HAVING count(*) > 1)"
                            ).fetchone()[0]
        if dupes:
            problems.append(f"{table} has {dupes} duplicated keys")
    return problems


def print_summary(con, eia_rows, forecast_rows, backtest_rows, origin):
    print("Row counts (database vs CSV):")
    future = [r for r in forecast_rows if r["record_type"] == "forecast"]
    checks = [
        ("eia_actuals_monthly", "SELECT count(*) FROM eia_actuals_monthly", len(eia_rows), "actual months"),
        ("forecast_runs", "SELECT count(*) FROM forecast_runs WHERE forecast_origin = ?", 1, "forecast run"),
        ("forecast_scenarios_monthly", "SELECT count(*) FROM forecast_scenarios_monthly WHERE forecast_origin = ?",
         len(future) * 3, f"{len(future)} months x 3 scenarios"),
        ("backtest_results", "SELECT count(*) FROM backtest_results WHERE forecast_origin = ?",
         len(backtest_rows), "backtest rows"),
    ]
    for table, sql, expected, meaning in checks:
        params = [month_date(origin)] if "?" in sql else []
        actual = con.execute(sql, params).fetchone()[0]
        print(f"  {table:<28} {actual:>4}   CSV: {expected:>4} ({meaning})")

    total_runs = con.execute("SELECT count(*) FROM forecast_runs").fetchone()[0]
    print(f"\nForecast runs stored: {total_runs} (this run: data through {origin})")


def main():
    eia_rows = read_csv(EIA_FILE)
    forecast_rows = read_csv(FORECAST_FILE)
    backtest_rows = read_csv(BACKTEST_FILE)

    origin = forecast_rows[-1]["forecast_made_from_data_through"]
    if origin != eia_rows[-1]["month"]:
        sys.exit(f"The forecast was built from data through {origin}, but the EIA file runs through "
                 f"{eia_rows[-1]['month']}. Run 'python forecast.py' first, then load again.")

    DATABASE_FILE.parent.mkdir(exist_ok=True)
    try:
        con = duckdb.connect(str(DATABASE_FILE))
    except duckdb.IOException as e:
        sys.exit(f"Could not open the database. Is another program using it?\n  {e}")

    con.execute(SCHEMA)
    con.execute("BEGIN TRANSACTION")
    load_actuals(con, eia_rows)
    load_forecast(con, forecast_rows, backtest_rows, origin)
    problems = verify(con, eia_rows, forecast_rows, backtest_rows, origin)
    if problems:
        fail(con, "\n  ".join(problems))
    con.execute("COMMIT")

    print(f"Loaded {DATABASE_FILE.relative_to(PROJECT_DIR)}\n")
    print_summary(con, eia_rows, forecast_rows, backtest_rows, origin)
    print("Every loaded row matches its source CSV. No duplicate keys.")
    con.close()


if __name__ == "__main__":
    main()
