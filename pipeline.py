"""
Energy finance pipeline - Milestone 1.

Reads monthly oil production and prices from a CSV, checks the data,
calculates revenue, costs and profit for each month, and writes the results
to a new CSV.

Uses only Python's standard library (nothing to pip install).

Run from the project folder:
    python pipeline.py
"""

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

# ---------------------------------------------------------------------------
# Settings: where files live
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent
INPUT_FILE = PROJECT_DIR / "data" / "sample_monthly_oil.csv"
OUTPUT_FILE = PROJECT_DIR / "output" / "monthly_results.csv"

# ---------------------------------------------------------------------------
# Assumptions: simple, illustrative numbers (not real company figures).
# Change these to test different scenarios.
# ---------------------------------------------------------------------------
ROYALTY_RATE = Decimal("0.125")               # 12.5% of revenue paid to the mineral owner
OPERATING_COST_PER_BARREL = Decimal("22.00")  # "lifting cost": $ spent to produce one barrel

REQUIRED_COLUMNS = ["month", "well_name", "barrels_produced", "price_per_barrel"]
CENTS = Decimal("0.01")


def to_money(amount):
    """Round a Decimal to 2 decimal places, the way accountants round."""
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Step 1: Extract - read the raw rows from the CSV
# ---------------------------------------------------------------------------
def read_rows(path):
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Input file is missing required columns: {missing}")
        return list(reader)


# ---------------------------------------------------------------------------
# Step 2: Validate - check every row before using it
# ---------------------------------------------------------------------------
def validate_rows(rows):
    """Return cleaned rows, or stop with a list of every problem found."""
    errors = []
    clean_rows = []
    seen = set()

    if not rows:
        raise ValueError("Input file has no data rows.")

    # Line 1 of the file is the header, so data starts on line 2.
    for line_number, row in enumerate(rows, start=2):
        month = (row.get("month") or "").strip()
        well = (row.get("well_name") or "").strip()

        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            errors.append(f"Line {line_number}: month '{month}' is not in YYYY-MM format")

        if not well:
            errors.append(f"Line {line_number}: well_name is blank")

        if (month, well) in seen:
            errors.append(f"Line {line_number}: duplicate entry for {well} in {month}")
        seen.add((month, well))

        barrels = parse_number(row.get("barrels_produced"), "barrels_produced", line_number, errors)
        price = parse_number(row.get("price_per_barrel"), "price_per_barrel", line_number, errors)

        clean_rows.append({
            "month": month,
            "well_name": well,
            "barrels_produced": barrels,
            "price_per_barrel": price,
        })

    if errors:
        raise ValueError("Data validation failed:\n  " + "\n  ".join(errors))
    return clean_rows


def parse_number(raw, column, line_number, errors):
    """Turn text like '74.20' into a Decimal. Record an error if it isn't a valid, non-negative number."""
    text = (raw or "").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        errors.append(f"Line {line_number}: {column} '{text}' is not a number")
        return None
    if not value.is_finite() or value < 0:
        errors.append(f"Line {line_number}: {column} '{text}' must be zero or positive")
        return None
    return value


# ---------------------------------------------------------------------------
# Step 3: Transform - calculate revenue, costs and profit
# ---------------------------------------------------------------------------
def calculate_results(rows):
    results = []
    for row in rows:
        revenue = row["barrels_produced"] * row["price_per_barrel"]
        royalty = revenue * ROYALTY_RATE
        operating_cost = row["barrels_produced"] * OPERATING_COST_PER_BARREL
        profit = revenue - royalty - operating_cost
        margin_pct = (profit / revenue * 100) if revenue else Decimal("0")

        results.append({
            "month": row["month"],
            "well_name": row["well_name"],
            "barrels_produced": row["barrels_produced"],
            "price_per_barrel": to_money(row["price_per_barrel"]),
            "revenue": to_money(revenue),
            "royalty": to_money(royalty),
            "operating_cost": to_money(operating_cost),
            "profit": to_money(profit),
            "profit_margin_pct": to_money(margin_pct),
        })
    return results


# ---------------------------------------------------------------------------
# Step 4: Load - write results to a new CSV
# ---------------------------------------------------------------------------
def write_results(results, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)


def print_summary(results):
    total_barrels = sum(r["barrels_produced"] for r in results)
    total_revenue = sum(r["revenue"] for r in results)
    total_profit = sum(r["profit"] for r in results)

    print(f"{'Month':<8} {'Barrels':>8} {'Price':>8} {'Revenue':>14} {'Profit':>14} {'Margin':>7}")
    for r in results:
        print(f"{r['month']:<8} {r['barrels_produced']:>8,} {r['price_per_barrel']:>8,} "
              f"{r['revenue']:>14,} {r['profit']:>14,} {r['profit_margin_pct']:>6}%")
    print("-" * 64)
    print(f"Total barrels:  {total_barrels:,}")
    print(f"Total revenue:  ${total_revenue:,}")
    print(f"Total profit:   ${total_profit:,}")
    print(f"Overall margin: {to_money(total_profit / total_revenue * 100)}%")


def main():
    print(f"Reading {INPUT_FILE.relative_to(PROJECT_DIR)} (fictional sample data)\n")
    rows = read_rows(INPUT_FILE)
    clean_rows = validate_rows(rows)
    results = calculate_results(clean_rows)
    write_results(results, OUTPUT_FILE)
    print_summary(results)
    print(f"\nWrote {len(results)} rows to {OUTPUT_FILE.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
