"""
EIA pipeline - Milestone 2.

Downloads official U.S. Energy Information Administration (EIA) data:
  - monthly U.S. field production of crude oil
  - monthly WTI crude oil spot price (Cushing, Oklahoma)
saves the raw API responses, validates them, joins them by month, and writes
a CSV with production, price, and an illustrative production value.

"Production value" = barrels produced x WTI spot price. It is a rough,
national-level market value of the oil produced. It is NOT the revenue or
profit of any company, and no royalty or operating cost is applied.

This pipeline is separate from pipeline.py, which uses fictional well data.

Needs a free EIA API key in the EIA_API_KEY environment variable
(see README.md). The key is never written to any file.

Run from the project folder:
    python eia_pipeline.py
"""

import calendar
import csv
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent
RAW_DIR = PROJECT_DIR / "data" / "raw" / "eia"
OUTPUT_FILE = PROJECT_DIR / "output" / "eia_us_crude_production_value.csv"

API_BASE = "https://api.eia.gov/v2"
START_MONTH = "2023-01"      # first month to request
MIN_MATCHING_MONTHS = 24     # fail if fewer months have both production and price

# The EIA series we use. Units are checked against what the API returns.
PRODUCTION_ROUTE = "petroleum/crd/crpdn"
PRODUCTION_SERIES = "MCRFPUS1"          # U.S. Field Production of Crude Oil
PRODUCTION_UNITS = "MBBL"               # thousand barrels (total for the month)
PRODUCTION_PER_DAY_SERIES = "MCRFPUS2"  # same thing, per day; used as a cross-check
PRODUCTION_PER_DAY_UNITS = "MBBL/D"     # thousand barrels per day

PRICE_ROUTE = "petroleum/pri/spt"
PRICE_SERIES = "RWTC"                   # Cushing, OK WTI Spot Price FOB
PRICE_UNITS = "$/BBL"                   # dollars per barrel

# Monthly total and (per-day x days in month) should agree closely.
# They differ slightly only because EIA rounds each to whole thousands.
CROSS_CHECK_TOLERANCE = Decimal("0.005")  # 0.5%


# ---------------------------------------------------------------------------
# Step 1: Extract - download from the EIA API and save the raw responses
# ---------------------------------------------------------------------------
def get_api_key():
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key:
        sys.exit(
            "EIA_API_KEY is not set.\n"
            "Get a free key at https://www.eia.gov/opendata/register.php, then run:\n"
            "    export EIA_API_KEY='your-key-here'\n"
            "    python eia_pipeline.py"
        )
    return key


def build_params(series_ids):
    """Query parameters for one request, without the API key."""
    params = [
        ("frequency", "monthly"),
        ("data[0]", "value"),
        ("start", START_MONTH),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("length", "5000"),
    ]
    for series_id in series_ids:
        params.append(("facets[series][]", series_id))
    return params


def make_ssl_context():
    """
    Secure HTTPS settings. Python installed from python.org on a Mac may not
    have its own certificate list yet; if so, use the one macOS provides.
    Certificates are always verified.
    """
    context = ssl.create_default_context()
    macos_certs = Path("/etc/ssl/cert.pem")
    if not context.get_ca_certs() and macos_certs.exists():
        context.load_verify_locations(cafile=str(macos_certs))
    return context


# EIA's API gateway returns one of these codes in the error body.
# Meanings from https://api.data.gov/docs/developer-manual/ (General Web Service Errors).
API_ERROR_HINTS = {
    "API_KEY_MISSING": "No key reached EIA. Check that EIA_API_KEY is exported in this Terminal window.",
    "API_KEY_INVALID": "EIA does not recognize this key. Check it was copied exactly from EIA's email.",
    "API_KEY_DISABLED": "EIA has disabled this key. Register again or contact EIA.",
    "API_KEY_UNAUTHORIZED": "This key is not authorized for this EIA data. Contact EIA.",
    "API_KEY_UNVERIFIED": "This key has not been verified yet. Look for a verification email from EIA.",
    "OVER_RATE_LIMIT": "Rate limit reached. Wait an hour, or use your own key instead of DEMO_KEY.",
}


def describe_key_shape(api_key):
    """Facts about the key's format that help spot copy/paste problems, without revealing the key."""
    issues = []
    if any(c in api_key for c in "'\"`"):
        issues.append("contains quote marks (they became part of the key)")
    if any(c.isspace() for c in api_key):
        issues.append("contains spaces or line breaks")
    if not api_key.isascii():
        issues.append("contains non-ASCII characters (for example smart quotes or invisible characters)")
    if "api_key" in api_key.lower() or "=" in api_key:
        issues.append("contains 'api_key' or '=' (only the key itself should be pasted)")
    other = {c for c in api_key if c.isascii() and not c.isalnum() and not c.isspace() and c not in "'\"`="}
    if other:
        issues.append(f"contains {len(other)} kind(s) of punctuation")
    shape = f"Key format check: {len(api_key)} characters"
    shape += ", only letters and digits" if api_key.isalnum() and api_key.isascii() else ""
    return shape + ("; " + "; ".join(issues) if issues else "") + ". (The key itself is not shown.)"


def explain_http_error(error, api_key):
    """EIA's own error code and message, with the key redacted if it ever appears."""
    try:
        body = error.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    body = body.replace(api_key, "[REDACTED]") if api_key else body
    try:
        details = json.loads(body).get("error", {})
        code, message = details.get("code", "unknown"), details.get("message", "")
    except (json.JSONDecodeError, AttributeError):
        code, message = "unknown", body[:200]

    lines = [f"EIA error code: {code}", f"EIA message: {message}"]
    if code in API_ERROR_HINTS:
        lines.append(f"What it means: {API_ERROR_HINTS[code]}")
    if code == "API_KEY_INVALID":
        lines.append(describe_key_shape(api_key))
    return "\n".join(lines)


def download(route, series_ids, api_key):
    """Call the EIA API. Returns (raw response text, public URL without the key)."""
    params = build_params(series_ids)
    public_url = f"{API_BASE}/{route}/data/?" + urllib.parse.urlencode(params)
    request_url = public_url + "&" + urllib.parse.urlencode({"api_key": api_key})

    try:
        with urllib.request.urlopen(request_url, timeout=60, context=make_ssl_context()) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # Deliberately don't print request_url: it contains the key.
        sys.exit(f"EIA API returned HTTP {e.code} for {public_url}\n" + explain_http_error(e, api_key))
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the EIA API: {e.reason}")

    if api_key in text:
        sys.exit("Safety stop: the API response contains the API key, so it was not saved.")
    return text, public_url


def save_raw(name, text, public_url, downloaded_at):
    """Save the untouched API response plus a small file describing where it came from."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = downloaded_at.strftime("%Y-%m-%d")
    data_path = RAW_DIR / f"{name}_{stamp}.json"
    data_path.write_text(text, encoding="utf-8")

    source_info = {
        "source": "U.S. Energy Information Administration (EIA) Open Data API v2",
        "url_without_api_key": public_url,
        "downloaded_at_utc": downloaded_at.isoformat(timespec="seconds"),
    }
    (RAW_DIR / f"{name}_{stamp}.source.json").write_text(
        json.dumps(source_info, indent=2), encoding="utf-8"
    )
    return data_path


# ---------------------------------------------------------------------------
# Step 2: Validate - turn raw records into clean {month: value} tables
# ---------------------------------------------------------------------------
def parse_response(text, label):
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        sys.exit(f"{label}: response is not valid JSON")
    if "error" in payload:
        sys.exit(f"{label}: EIA API error: {payload['error']}")
    records = payload.get("response", {}).get("data")
    if not records:
        sys.exit(f"{label}: response contains no data records")
    return records


def extract_series(records, series_id, expected_units, errors):
    """Pick one series out of the records and check each value. Returns {month: Decimal}."""
    values = {}
    for record in records:
        if record.get("series") != series_id:
            continue
        month = record.get("period", "")
        where = f"{series_id} {month}"

        if record.get("units") != expected_units:
            errors.append(f"{where}: units are '{record.get('units')}', expected '{expected_units}'")
            continue
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            errors.append(f"{where}: period is not in YYYY-MM format")
            continue
        if month in values:
            errors.append(f"{where}: duplicate month")
            continue
        try:
            value = Decimal(str(record.get("value")))
        except InvalidOperation:
            errors.append(f"{where}: value '{record.get('value')}' is not a number")
            continue
        if not value.is_finite() or value <= 0:
            errors.append(f"{where}: value '{record.get('value')}' must be a positive number")
            continue
        values[month] = value

    if not values:
        errors.append(f"{series_id}: no records found")
    return values


def days_in_month(month):
    year, mon = int(month[:4]), int(month[5:7])
    return calendar.monthrange(year, mon)[1]


def cross_check_production(monthly_total, per_day, errors):
    """Confirm the unit conversion: thousand bbl/day x days should equal thousand bbl for the month."""
    for month, total in monthly_total.items():
        if month not in per_day:
            continue
        implied_total = per_day[month] * days_in_month(month)
        difference = abs(implied_total - total) / total
        if difference > CROSS_CHECK_TOLERANCE:
            errors.append(
                f"{month}: production cross-check failed: {per_day[month]} MBBL/D x "
                f"{days_in_month(month)} days = {implied_total} MBBL, but monthly total is {total} MBBL"
            )


# ---------------------------------------------------------------------------
# Step 3: Transform - join by month and calculate production value
# ---------------------------------------------------------------------------
def join_and_calculate(production_mbbl, production_mbbl_per_day, price):
    rows = []
    for month in sorted(set(production_mbbl) & set(price)):
        barrels = production_mbbl[month] * 1000   # thousand barrels -> barrels
        value_usd = barrels * price[month]        # barrels x $/barrel -> $
        rows.append({
            "month": month,
            "us_crude_production_thousand_bbl": production_mbbl[month],
            "us_crude_production_thousand_bbl_per_day": production_mbbl_per_day.get(month, ""),
            "us_crude_production_bbl": barrels,
            "wti_spot_price_usd_per_bbl": price[month],
            "illustrative_production_value_usd": value_usd.quantize(Decimal("1"), rounding=ROUND_HALF_UP),
            "illustrative_production_value_usd_billions": (value_usd / Decimal("1e9")).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP),
        })
    return rows


def check_months_are_consecutive(rows, errors):
    months = [r["month"] for r in rows]
    for earlier, later in zip(months, months[1:]):
        y, m = int(earlier[:4]), int(earlier[5:7])
        expected = f"{y + (m == 12)}-{(m % 12) + 1:02d}"
        if later != expected:
            errors.append(f"Gap in joined data: {earlier} is followed by {later}")


# ---------------------------------------------------------------------------
# Step 4: Load - write the output CSV
# ---------------------------------------------------------------------------
def write_results(rows, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    api_key = get_api_key()
    downloaded_at = datetime.now(timezone.utc)

    print("Downloading official EIA data...")
    production_text, production_url = download(
        PRODUCTION_ROUTE, [PRODUCTION_SERIES, PRODUCTION_PER_DAY_SERIES], api_key)
    price_text, price_url = download(PRICE_ROUTE, [PRICE_SERIES], api_key)

    production_file = save_raw("us_crude_production", production_text, production_url, downloaded_at)
    price_file = save_raw("wti_spot_price", price_text, price_url, downloaded_at)
    print(f"  Saved raw data: {production_file.relative_to(PROJECT_DIR)}")
    print(f"  Saved raw data: {price_file.relative_to(PROJECT_DIR)}")

    errors = []
    production_records = parse_response(production_text, "Production")
    price_records = parse_response(price_text, "Price")
    production_mbbl = extract_series(production_records, PRODUCTION_SERIES, PRODUCTION_UNITS, errors)
    production_mbbl_per_day = extract_series(
        production_records, PRODUCTION_PER_DAY_SERIES, PRODUCTION_PER_DAY_UNITS, errors)
    price = extract_series(price_records, PRICE_SERIES, PRICE_UNITS, errors)
    cross_check_production(production_mbbl, production_mbbl_per_day, errors)

    rows = join_and_calculate(production_mbbl, production_mbbl_per_day, price)
    check_months_are_consecutive(rows, errors)
    if len(rows) < MIN_MATCHING_MONTHS:
        errors.append(f"Only {len(rows)} months have both production and price; need {MIN_MATCHING_MONTHS}")

    if errors:
        sys.exit("Data validation failed:\n  " + "\n  ".join(errors))

    write_results(rows, OUTPUT_FILE)

    print(f"\nValidation passed. Production and price joined for {len(rows)} months "
          f"({rows[0]['month']} to {rows[-1]['month']}).")
    only_price = sorted(set(price) - set(production_mbbl))
    if only_price:
        print(f"Price months without production data yet (not in output): {', '.join(only_price)}")

    print(f"\n{'Month':<8} {'Production (MBBL)':>18} {'WTI $/bbl':>10} {'Value ($bn)':>12}")
    for r in rows[-3:]:
        print(f"{r['month']:<8} {r['us_crude_production_thousand_bbl']:>18,} "
              f"{r['wti_spot_price_usd_per_bbl']:>10} {r['illustrative_production_value_usd_billions']:>12}")
    print(f"\nWrote {len(rows)} rows to {OUTPUT_FILE.relative_to(PROJECT_DIR)}")
    print("Note: production value is illustrative (volume x spot price), not company revenue or profit.")


if __name__ == "__main__":
    main()
