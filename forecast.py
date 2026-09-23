"""
Forecasting module - Milestone 3.

Uses only the validated EIA output from eia_pipeline.py to forecast, for the
next 12 months:
  - U.S. crude oil production (thousand barrels per day)
  - WTI spot price (dollars per barrel)
and then an illustrative production value (volume x price).

Steps:
  1. Load and re-check the validated EIA data.
  2. Backtest simple baseline methods: pretend we are standing in an earlier
     month, forecast using only data up to that month, and compare with what
     actually happened.
  3. Pick the method with the lowest average backtest error for each series.
  4. Forecast 12 months ahead with low / base / high scenarios.
  5. Write backtest results and forecasts to CSV.

Production value is NOT company revenue or profit.

Run from the project folder (after eia_pipeline.py):
    python forecast.py
"""

import csv
import math
import sys
from decimal import Decimal
from pathlib import Path

from eia_pipeline import CROSS_CHECK_TOLERANCE, check_months_are_consecutive, days_in_month
from eia_pipeline import OUTPUT_FILE as EIA_OUTPUT_FILE

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent
BACKTEST_FILE = PROJECT_DIR / "output" / "forecast_backtest.csv"
FORECAST_FILE = PROJECT_DIR / "output" / "forecast_12_months.csv"

HORIZON = 12               # months to forecast
MIN_TRAINING_MONTHS = 13   # the 12-month drift method needs 13 months of history
SCENARIO_PERCENTILE = 0.90 # low/high use the 90th percentile of backtest % errors
MIN_INPUT_MONTHS = 24

# The two series we forecast: (column in EIA output, readable name, units)
SERIES = {
    "production": ("us_crude_production_thousand_bbl_per_day", "U.S. crude production", "thousand bbl/day"),
    "price": ("wti_spot_price_usd_per_bbl", "WTI spot price", "$/bbl"),
}


# ---------------------------------------------------------------------------
# Baseline forecasting methods
# Each takes the history known so far (oldest first) and returns HORIZON
# forecasts. They can only see the list they are given.
# ---------------------------------------------------------------------------
def naive(history, horizon):
    """Next months = the last known month. ("Tomorrow looks like today.")"""
    return [history[-1]] * horizon


def average_3_months(history, horizon):
    """Next months = the average of the last 3 months. Smooths out one-off blips."""
    return [sum(history[-3:]) / 3] * horizon


def drift_12_months(history, horizon):
    """Continue the average monthly change of the last 12 months in a straight line."""
    monthly_change = (history[-1] - history[-13]) / 12
    return [history[-1] + monthly_change * h for h in range(1, horizon + 1)]


METHODS = {
    "naive_last_value": naive,
    "average_last_3_months": average_3_months,
    "drift_last_12_months": drift_12_months,
}


# ---------------------------------------------------------------------------
# Step 1: Load and re-check the validated EIA data
# ---------------------------------------------------------------------------
def next_month(month):
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year + (mon == 12)}-{(mon % 12) + 1:02d}"


def load_eia_data(path):
    if not path.exists():
        sys.exit(f"{path.relative_to(PROJECT_DIR)} not found. Run 'python eia_pipeline.py' first.")

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    errors = []
    for row in rows:
        month = row["month"]
        monthly_mbbl = Decimal(row["us_crude_production_thousand_bbl"])
        per_day = Decimal(row["us_crude_production_thousand_bbl_per_day"])
        price = Decimal(row["wti_spot_price_usd_per_bbl"])
        value_usd = Decimal(row["illustrative_production_value_usd"])

        if min(monthly_mbbl, per_day, price) <= 0:
            errors.append(f"{month}: production and price must be positive")
            continue
        if abs(per_day * days_in_month(month) - monthly_mbbl) / monthly_mbbl > CROSS_CHECK_TOLERANCE:
            errors.append(f"{month}: per-day production x days does not match the monthly total")
        if abs(monthly_mbbl * 1000 * price - value_usd) > 1:
            errors.append(f"{month}: production value does not equal barrels x price")

    check_months_are_consecutive(rows, errors)
    if len(rows) < MIN_INPUT_MONTHS:
        errors.append(f"Only {len(rows)} months of data; need at least {MIN_INPUT_MONTHS}")
    if errors:
        sys.exit("Input check failed:\n  " + "\n  ".join(errors))
    return rows


# ---------------------------------------------------------------------------
# Step 2: Backtest
# ---------------------------------------------------------------------------
def backtest(values, months, method):
    """
    Rolling-origin backtest. For each starting month ("origin"), forecast the
    next 12 months using ONLY values up to and including the origin, then
    compare with the actual values. Returns {horizon: [(actual, forecast), ...]}.
    """
    results = {h: [] for h in range(1, HORIZON + 1)}
    for origin in range(MIN_TRAINING_MONTHS - 1, len(values) - 1):
        history = values[: origin + 1]  # nothing after the origin month
        forecasts = method(history, HORIZON)
        for h, forecast in enumerate(forecasts, start=1):
            target = origin + h
            if target >= len(values):
                break
            assert months[target] > months[origin]  # never compare against the past
            results[h].append((values[target], forecast))
    return results


def percentile(sorted_values, p):
    """Nearest-rank percentile of an already sorted list."""
    return sorted_values[max(0, math.ceil(p * len(sorted_values)) - 1)]


def summarize(pairs):
    """Error statistics for a list of (actual, forecast) pairs."""
    errors = [f - a for a, f in pairs]
    pct_errors = [(f - a) / a * 100 for a, f in pairs]
    abs_pct = sorted(abs(e) for e in pct_errors)
    return {
        "n_forecasts": len(pairs),
        "mae": sum(abs(e) for e in errors) / len(errors),
        "mape_pct": sum(abs_pct) / len(abs_pct),
        "bias_pct": sum(pct_errors) / len(pct_errors),
        "p90_abs_pct_error": percentile(abs_pct, SCENARIO_PERCENTILE),
    }


def run_backtests(data_rows):
    months = [r["month"] for r in data_rows]
    table = []    # rows for the backtest CSV
    chosen = {}   # series -> (method name, {horizon: stats})
    for key, (column, _, units) in SERIES.items():
        values = [float(r[column]) for r in data_rows]
        best = None
        for name, method in METHODS.items():
            by_horizon = {h: summarize(p) for h, p in backtest(values, months, method).items()}
            average_mape = sum(s["mape_pct"] for s in by_horizon.values()) / HORIZON
            for h, stats in by_horizon.items():
                table.append({"series": key, "units": units, "method": name, "horizon_months": h, **stats})
            if best is None or average_mape < best[1]:
                best = (name, average_mape, by_horizon)
        chosen[key] = (best[0], best[2])
    for row in table:
        row["selected_for_forecast"] = "yes" if chosen[row["series"]][0] == row["method"] else ""
    return table, chosen


# ---------------------------------------------------------------------------
# Step 3: Forecast with low / base / high scenarios
# ---------------------------------------------------------------------------
def build_forecast(data_rows, chosen):
    """
    Base = the selected method's forecast.
    Low / high = base x (1 -/+ the 90th-percentile absolute % error that the
    same method made at the same horizon in the backtest). The band never
    narrows as the horizon gets longer: uncertainty only grows with time.
    """
    last_month = data_rows[-1]["month"]
    scenarios = {}
    for key, (column, _, _) in SERIES.items():
        method_name, stats = chosen[key]
        base = METHODS[method_name]([float(r[column]) for r in data_rows], HORIZON)
        scenarios[key] = []
        band = 0.0
        for h, b in enumerate(base, start=1):
            band = max(band, stats[h]["p90_abs_pct_error"] / 100)
            scenarios[key].append({"low": b * (1 - band), "base": b, "high": b * (1 + band)})

    rows = []
    month = last_month
    for i in range(HORIZON):
        month = next_month(month)
        days = days_in_month(month)
        row = {"month": month, "record_type": "forecast", "forecast_made_from_data_through": last_month,
               "days_in_month": days}
        for case in ("low", "base", "high"):
            per_day = scenarios["production"][i][case]
            price = scenarios["price"][i][case]
            # thousand bbl/day x days x 1,000 = barrels; barrels x $/bbl = $
            value_usd = per_day * days * 1000 * price
            row[f"forecast_production_thousand_bbl_per_day_{case}"] = round(per_day, 1)
            row[f"forecast_wti_usd_per_bbl_{case}"] = round(price, 2)
            row[f"forecast_production_value_usd_billions_{case}"] = round(value_usd / 1e9, 3)
        rows.append(row)
    return rows


def actual_rows(data_rows):
    return [{
        "month": r["month"],
        "record_type": "actual",
        "days_in_month": days_in_month(r["month"]),
        "actual_production_thousand_bbl_per_day": r["us_crude_production_thousand_bbl_per_day"],
        "actual_wti_usd_per_bbl": r["wti_spot_price_usd_per_bbl"],
        "actual_production_value_usd_billions": r["illustrative_production_value_usd_billions"],
    } for r in data_rows]


def check_forecast_dates(data_rows, forecast_rows):
    expected = next_month(data_rows[-1]["month"])
    errors = []
    if forecast_rows[0]["month"] != expected:
        errors.append(f"First forecast month is {forecast_rows[0]['month']}, expected {expected}")
    if len(forecast_rows) != HORIZON:
        errors.append(f"Expected {HORIZON} forecast months, got {len(forecast_rows)}")
    check_months_are_consecutive(forecast_rows, errors)
    if errors:
        sys.exit("Forecast date check failed:\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------------------
# Step 4: Write outputs and print a summary
# ---------------------------------------------------------------------------
def write_csv(rows, path, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()})


def print_backtest(table):
    print(f"Backtest: average error by forecast horizon (lower is better)\n")
    for key, (_, name, units) in SERIES.items():
        print(f"{name} ({units})")
        print(f"  {'Method':<24} {'MAPE 1m':>8} {'3m':>7} {'6m':>7} {'12m':>7} {'Avg 1-12':>9}")
        for method in METHODS:
            rows = {r["horizon_months"]: r for r in table if r["series"] == key and r["method"] == method}
            avg = sum(r["mape_pct"] for r in rows.values()) / HORIZON
            mark = "  <- selected" if rows[1]["selected_for_forecast"] else ""
            print(f"  {method:<24} " + " ".join(f"{rows[h]['mape_pct']:>6.1f}%" for h in (1, 3, 6, 12))
                  + f" {avg:>8.1f}%{mark}")
        print()


def main():
    data_rows = load_eia_data(EIA_OUTPUT_FILE)
    print(f"Loaded {len(data_rows)} months of validated EIA data "
          f"({data_rows[0]['month']} to {data_rows[-1]['month']}).\n")

    table, chosen = run_backtests(data_rows)
    print_backtest(table)

    forecast_rows = build_forecast(data_rows, chosen)
    check_forecast_dates(data_rows, forecast_rows)

    write_csv(table, BACKTEST_FILE, list(table[0].keys()))
    all_rows = actual_rows(data_rows) + forecast_rows
    fieldnames = list(actual_rows(data_rows)[0].keys())[:3] + ["forecast_made_from_data_through"] \
        + list(actual_rows(data_rows)[0].keys())[3:] + list(forecast_rows[0].keys())[4:]
    write_csv(all_rows, FORECAST_FILE, fieldnames)

    print(f"Forecast (made from data through {data_rows[-1]['month']}), first 3 months:")
    print(f"  {'Month':<8} {'Production kbd (low/base/high)':<32} {'WTI $/bbl (low/base/high)':<28} Value $bn (low/base/high)")
    for r in forecast_rows[:3]:
        prod = "/".join(f"{r[f'forecast_production_thousand_bbl_per_day_{c}']:,.0f}" for c in ("low", "base", "high"))
        price = "/".join(f"{r[f'forecast_wti_usd_per_bbl_{c}']:.2f}" for c in ("low", "base", "high"))
        value = "/".join(f"{r[f'forecast_production_value_usd_billions_{c}']:.1f}" for c in ("low", "base", "high"))
        print(f"  {r['month']:<8} {prod:<32} {price:<28} {value}")

    print(f"\nWrote {BACKTEST_FILE.relative_to(PROJECT_DIR)}")
    print(f"Wrote {FORECAST_FILE.relative_to(PROJECT_DIR)} "
          f"({len(data_rows)} actual rows + {len(forecast_rows)} forecast rows)")
    print("Note: WTI prices are highly uncertain; scenarios reflect only 2023-2026 backtest errors.")
    print("Production value is illustrative (volume x price), not company revenue or profit.")


if __name__ == "__main__":
    main()
