"""
Snapshot maker.

Copies the validated EIA and forecast outputs into a dated folder,
data/snapshots/eia_<download date>/, which is committed to Git so the
dashboard has something to show on a fresh copy of the project, before
anyone has an EIA API key.

The snapshot contains only:
  - the three validated CSVs the dashboard needs
  - snapshot_info.json: EIA attribution, download time, data coverage,
    source URLs (without the API key) and file checksums

It never copies raw API responses, the database, or anything containing a key.

Run from the project folder after a successful refresh:
    python eia_pipeline.py && python forecast.py && python make_snapshot.py
"""

import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from forecast import load_eia_data

PROJECT_DIR = Path(__file__).parent
OUTPUT_DIR = PROJECT_DIR / "output"
RAW_DIR = PROJECT_DIR / "data" / "raw" / "eia"
SNAPSHOT_ROOT = PROJECT_DIR / "data" / "snapshots"

SNAPSHOT_FILES = [
    "eia_us_crude_production_value.csv",  # validated actuals (eia_pipeline.py)
    "forecast_12_months.csv",             # actuals + 12-month scenarios (forecast.py)
    "forecast_backtest.csv",              # backtest errors (forecast.py)
]

SERIES = {
    "us_crude_production": [
        {"series": "MCRFPUS1", "description": "U.S. Field Production of Crude Oil", "units": "thousand barrels"},
        {"series": "MCRFPUS2", "description": "U.S. Field Production of Crude Oil",
         "units": "thousand barrels per day"},
    ],
    "wti_spot_price": [
        {"series": "RWTC", "description": "Cushing, OK WTI Spot Price FOB", "units": "dollars per barrel"},
    ],
}


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    missing = [name for name in SNAPSHOT_FILES if not (OUTPUT_DIR / name).exists()]
    if missing:
        sys.exit(f"Missing outputs: {', '.join(missing)}. Run eia_pipeline.py and forecast.py first.")

    # Re-check the actuals (consecutive months, units, value = barrels x price) before publishing them.
    actuals = load_eia_data(OUTPUT_DIR / "eia_us_crude_production_value.csv")
    forecast_rows = read_rows(OUTPUT_DIR / "forecast_12_months.csv")
    future = [r for r in forecast_rows if r["record_type"] == "forecast"]
    origin = future[0]["forecast_made_from_data_through"]
    if origin != actuals[-1]["month"]:
        sys.exit(f"Forecast is built from data through {origin}, but actuals run through "
                 f"{actuals[-1]['month']}. Run 'python forecast.py' first.")

    # Download details come from the metadata eia_pipeline.py saved next to the raw data.
    sources = []
    for name, series in SERIES.items():
        info_files = sorted(RAW_DIR.glob(f"{name}_*.source.json"))
        if not info_files:
            sys.exit(f"No download details for {name} in {RAW_DIR.relative_to(PROJECT_DIR)}. "
                     f"Run 'python eia_pipeline.py' first.")
        info = json.loads(info_files[-1].read_text(encoding="utf-8"))
        sources.append({"dataset": name, "series": series, **info})

    downloaded = min(s["downloaded_at_utc"] for s in sources)
    download_date = datetime.fromisoformat(downloaded)
    snapshot_dir = SNAPSHOT_ROOT / f"eia_{download_date:%Y-%m-%d}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    for name in SNAPSHOT_FILES:
        shutil.copy2(OUTPUT_DIR / name, snapshot_dir / name)

    snapshot_info = {
        "status": "HISTORICAL SNAPSHOT - not live data",
        "purpose": "Lets the dashboard run on a fresh copy of the project before the EIA pipeline has been run.",
        "attribution": f"Source: U.S. Energy Information Administration ({download_date:%b %Y}). "
                       "EIA data is in the public domain; this project is not affiliated with or endorsed by EIA.",
        "eia_downloaded_at_utc": downloaded,
        "snapshot_created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actuals": {"first_month": actuals[0]["month"], "last_month": actuals[-1]["month"],
                    "months": len(actuals)},
        "forecast": {"made_from_data_through": origin, "first_month": future[0]["month"],
                     "last_month": future[-1]["month"], "months": len(future)},
        "note": "Production value is illustrative (national production x WTI price). "
                "It is not company revenue or profit.",
        "sources": sources,
        "files": {name: {"rows": len(read_rows(snapshot_dir / name)), "sha256": sha256(snapshot_dir / name)}
                  for name in SNAPSHOT_FILES},
    }
    text = json.dumps(snapshot_info, indent=2)
    if "api_key=" in text.lower():
        shutil.rmtree(snapshot_dir)
        sys.exit("Safety stop: snapshot metadata contains an API key parameter, so the snapshot was removed.")
    (snapshot_dir / "snapshot_info.json").write_text(text + "\n", encoding="utf-8")

    print(f"Snapshot written to {snapshot_dir.relative_to(PROJECT_DIR)}/")
    print(f"  EIA data downloaded {downloaded}; actuals {actuals[0]['month']} to {actuals[-1]['month']}; "
          f"forecast {future[0]['month']} to {future[-1]['month']}")
    for name, details in snapshot_info["files"].items():
        print(f"  {name}: {details['rows']} rows")


if __name__ == "__main__":
    main()
