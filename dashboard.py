"""
Executive dashboard - Milestone 4.

A local Streamlit app that displays the outputs of eia_pipeline.py and
forecast.py. It only reads those files; every number shown was calculated
and validated by the pipelines.

Data source:
  - Live: output/*.csv, when all three pipeline outputs exist.
  - Otherwise, the newest committed snapshot in data/snapshots/, clearly
    labelled as historical, so a fresh copy of the project can show something
    before anyone has run the pipelines.

Launch from the project folder:
    streamlit run dashboard.py
"""

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from forecast import HORIZON, METHODS, SCENARIO_PERCENTILE

PROJECT_DIR = Path(__file__).parent
OUTPUT_DIR = PROJECT_DIR / "output"
RAW_DIR = PROJECT_DIR / "data" / "raw" / "eia"
SNAPSHOT_ROOT = PROJECT_DIR / "data" / "snapshots"

# The three files the dashboard reads (same names in output/ and in each snapshot)
EIA_NAME = "eia_us_crude_production_value.csv"
FORECAST_NAME = "forecast_12_months.csv"
BACKTEST_NAME = "forecast_backtest.csv"
REQUIRED_NAMES = [EIA_NAME, FORECAST_NAME, BACKTEST_NAME]

ACTUAL_COLOR = "#1f4e79"
FORECAST_COLOR = "#d9822b"

# The three measures shown, with the column names used in forecast_12_months.csv
MEASURES = {
    "production": {
        "label": "U.S. crude production", "units": "thousand barrels per day", "axis": "Thousand bbl/day",
        "actual": "actual_production_thousand_bbl_per_day",
        "forecast": "forecast_production_thousand_bbl_per_day_{}", "format": ",.0f",
    },
    "price": {
        "label": "WTI spot price", "units": "dollars per barrel", "axis": "$ per barrel",
        "actual": "actual_wti_usd_per_bbl",
        "forecast": "forecast_wti_usd_per_bbl_{}", "format": "$,.2f",
    },
    "value": {
        "label": "Illustrative production value", "units": "billions of dollars", "axis": "$ billions",
        "actual": "actual_production_value_usd_billions",
        "forecast": "forecast_production_value_usd_billions_{}", "format": "$,.1f",
    },
}

NOT_REVENUE = (
    "**Production value is illustrative, not company revenue or profit.** It is national U.S. "
    "production multiplied by the WTI benchmark price, to show the scale of the market. No royalty, "
    "operating cost, tax or regional price difference is applied."
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
REFRESH_COMMANDS = (
    "cd ~/energy-finance-platform\nsource .venv/bin/activate\n"
    "read -rs EIA_API_KEY && export EIA_API_KEY   # paste your EIA key\n"
    "python eia_pipeline.py && python forecast.py"
)


def latest_snapshot():
    """Newest complete snapshot folder (names are eia_YYYY-MM-DD, so they sort by date), or None."""
    if not SNAPSHOT_ROOT.exists():
        return None
    complete = [d for d in sorted(SNAPSHOT_ROOT.iterdir())
                if d.is_dir() and (d / "snapshot_info.json").exists()
                and all((d / name).exists() for name in REQUIRED_NAMES)]
    return complete[-1] if complete else None


def choose_data_source():
    """
    Returns (mode, folder, missing_live_files). mode is "live" when all pipeline outputs
    exist, "snapshot" when falling back to a committed snapshot. Stops with instructions
    if neither is available.
    """
    missing_live = [name for name in REQUIRED_NAMES if not (OUTPUT_DIR / name).exists()]
    if not missing_live:
        return "live", OUTPUT_DIR, []
    snapshot = latest_snapshot()
    if snapshot:
        return "snapshot", snapshot, missing_live

    st.error("The dashboard needs pipeline output files that don't exist yet, and no snapshot was found.")
    st.markdown("Missing:\n" + "\n".join(f"- `output/{name}`" for name in missing_live))
    st.markdown("Run the pipelines from the project folder in Terminal, then refresh this page:")
    st.code(REFRESH_COMMANDS, language="bash")
    st.stop()


@st.cache_data
def load_csv(path, modified_time):
    """modified_time is only there so the cache refreshes when a pipeline rewrites the file."""
    df = pd.read_csv(path)
    if "month" in df.columns:
        df["date"] = pd.to_datetime(df["month"], format="%Y-%m")
    return df


def load(path):
    return load_csv(path, path.stat().st_mtime)


def load_source_info(mode, folder):
    """Download details: from the raw-data metadata (live) or from snapshot_info.json (snapshot)."""
    if mode == "snapshot":
        snapshot = json.loads((folder / "snapshot_info.json").read_text(encoding="utf-8"))
        return [{"file": s["dataset"], "downloaded_at_utc": s["downloaded_at_utc"],
                 "url_without_api_key": s["url_without_api_key"]} for s in snapshot["sources"]], snapshot
    info = []
    for path in sorted(RAW_DIR.glob("*.source.json")):
        details = json.loads(path.read_text(encoding="utf-8"))
        details["file"] = path.name.replace(".source.json", ".json")
        info.append(details)
    return info, None


def utc_label(timestamp):
    return pd.to_datetime(timestamp).strftime("%d %b %Y, %H:%M UTC")


def month_label(month):
    return pd.to_datetime(month, format="%Y-%m").strftime("%b %Y")


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def history_chart(df, column, measure):
    return alt.Chart(df).mark_line(point=True, color=ACTUAL_COLOR).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %Y")),
        y=alt.Y(f"{column}:Q", title=measure["axis"], scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("month:N", title="Month"),
                 alt.Tooltip(f"{column}:Q", title=measure["axis"], format=measure["format"])],
    ).properties(height=260)


def actual_vs_forecast_chart(forecast_df, measure, boundary_date, boundary_text):
    actual = forecast_df[forecast_df["record_type"] == "actual"]
    future = forecast_df[forecast_df["record_type"] == "forecast"]
    low, base, high = (measure["forecast"].format(c) for c in ("low", "base", "high"))
    y_title = measure["axis"]

    band = alt.Chart(future).mark_area(opacity=0.2, color=FORECAST_COLOR).encode(
        x="date:T", y=alt.Y(f"{low}:Q", title=y_title), y2=f"{high}:Q",
    )
    actual_line = alt.Chart(actual).mark_line(color=ACTUAL_COLOR).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %Y")),
        y=alt.Y(f"{measure['actual']}:Q", title=y_title, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("month:N", title="Month (actual)"),
                 alt.Tooltip(f"{measure['actual']}:Q", title="Actual", format=measure["format"])],
    )
    base_line = alt.Chart(future).mark_line(
        color=FORECAST_COLOR, strokeDash=[6, 4], point=alt.OverlayMarkDef(color=FORECAST_COLOR)).encode(
        x="date:T", y=f"{base}:Q",
        tooltip=[alt.Tooltip("month:N", title="Month (forecast)"),
                 alt.Tooltip(f"{low}:Q", title="Low", format=measure["format"]),
                 alt.Tooltip(f"{base}:Q", title="Base", format=measure["format"]),
                 alt.Tooltip(f"{high}:Q", title="High", format=measure["format"])],
    )
    boundary = pd.DataFrame({"date": [boundary_date], "text": [boundary_text]})
    rule = alt.Chart(boundary).mark_rule(color="gray", strokeDash=[3, 3]).encode(x="date:T")
    label = alt.Chart(boundary).mark_text(align="left", dx=5, dy=-120, color="gray").encode(
        x="date:T", text="text:N")
    return (band + actual_line + base_line + rule + label).properties(height=320)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Energy Finance Dashboard", layout="wide")
st.title("U.S. Crude Oil: Production, WTI Price & Illustrative Production Value")

mode, data_dir, missing_live = choose_data_source()
eia = load(data_dir / EIA_NAME)
forecast_df = load(data_dir / FORECAST_NAME)
backtest = load(data_dir / BACKTEST_NAME)
sources, snapshot_info = load_source_info(mode, data_dir)
is_snapshot = mode == "snapshot"

future = forecast_df[forecast_df["record_type"] == "forecast"]
last_actual_month = eia["month"].iloc[-1]
forecast_origin = future["forecast_made_from_data_through"].iloc[0]

if is_snapshot:
    snapshot_date = utc_label(snapshot_info["eia_downloaded_at_utc"])
    DATA_LABEL = f"Historical snapshot · EIA data downloaded {snapshot_date}"
    st.caption(f"Energy Finance & Data Analytics Platform · {DATA_LABEL} · "
               f"`{data_dir.relative_to(PROJECT_DIR)}`")
    st.warning(
        f"**Historical snapshot, not live data.** You are viewing a saved copy of the validated outputs, "
        f"made from EIA data downloaded on **{snapshot_date}**. Actuals run to "
        f"**{month_label(last_actual_month)}**, and the forecast was made from data through "
        f"**{month_label(forecast_origin)}**. Newer EIA data may exist.\n\n"
        f"It's shown because these live pipeline outputs are missing: "
        + ", ".join(f"`output/{name}`" for name in missing_live)
        + ". To see current data, get a free EIA key and run the pipelines, then refresh this page.",
        icon="🕰️",
    )
    with st.expander("How to load current data"):
        st.code(REFRESH_COMMANDS, language="bash")
        st.markdown("Free EIA API key: https://www.eia.gov/opendata/register.php")
else:
    download_times = [s["downloaded_at_utc"] for s in sources]
    DATA_LABEL = ("Live pipeline outputs" +
                  (f" · EIA data downloaded {utc_label(min(download_times))}" if download_times else ""))
    st.caption(f"Energy Finance & Data Analytics Platform · {DATA_LABEL}")

if forecast_origin != last_actual_month:
    st.warning(
        f"The forecast was built from data through {month_label(forecast_origin)}, but the EIA file now "
        f"runs through {month_label(last_actual_month)}. Run `python forecast.py` to refresh the forecast."
    )

st.warning(NOT_REVENUE, icon="⚠️")

# --- Headline numbers (latest actual month) --------------------------------
latest = eia.iloc[-1]
st.subheader(f"Latest actual month: {month_label(last_actual_month)}"
             + (" (historical snapshot)" if is_snapshot else ""))
c1, c2, c3, c4 = st.columns(4)
c1.metric("U.S. crude production", f"{latest['us_crude_production_thousand_bbl_per_day']:,.0f} kbd",
          help="Thousand barrels per day (EIA series MCRFPUS2)")
c2.metric("WTI spot price", f"${latest['wti_spot_price_usd_per_bbl']:,.2f} /bbl",
          help="Dollars per barrel, monthly average (EIA series RWTC)")
c3.metric("Illustrative production value", f"${latest['illustrative_production_value_usd_billions']:,.1f} bn",
          help="Monthly production × WTI price. Not company revenue or profit.")
c4.metric("Forecast period", f"{month_label(future['month'].iloc[0])} – {month_label(future['month'].iloc[-1])}",
          help=f"{len(future)} forecast months, made from data through {month_label(forecast_origin)}")

# --- Historical actuals ------------------------------------------------------
st.header("Historical actuals")
st.caption(f"{month_label(eia['month'].iloc[0])} – {month_label(last_actual_month)} · "
           f"{len(eia)} months of validated EIA data · {DATA_LABEL}")
history_columns = {
    "production": "us_crude_production_thousand_bbl_per_day",
    "price": "wti_spot_price_usd_per_bbl",
    "value": "illustrative_production_value_usd_billions",
}
for column, (key, eia_column) in zip(st.columns(3), history_columns.items()):
    measure = MEASURES[key]
    column.markdown(f"**{measure['label']}**  \n<small>{measure['units']}</small>", unsafe_allow_html=True)
    column.altair_chart(history_chart(eia, eia_column, measure), width="stretch")

# --- Actual vs forecast ------------------------------------------------------
st.header("Actual vs forecast")
st.markdown(
    f"Solid blue = **actual** EIA data. Dashed orange = **base forecast**; shaded area = **low–high scenario "
    f"range**. The dotted line marks the boundary: everything after **{month_label(forecast_origin)}** is a forecast."
)
if is_snapshot:
    st.caption(f"{DATA_LABEL}. This forecast was made from data through {month_label(forecast_origin)} "
               f"and has not been updated since.")
boundary_date = pd.to_datetime(forecast_origin, format="%Y-%m")
boundary_text = f"Forecast begins after {month_label(forecast_origin)} →"
for tab, key in zip(st.tabs([MEASURES[k]["label"] for k in MEASURES]), MEASURES):
    measure = MEASURES[key]
    tab.caption(f"Units: {measure['units']}")
    tab.altair_chart(actual_vs_forecast_chart(forecast_df, measure, boundary_date, boundary_text), width="stretch")

# --- Scenario table ----------------------------------------------------------
st.header(f"Next {len(future)} months: low, base and high scenarios")
st.caption("Every row below is a FORECAST, not actual data."
           + (f" From the historical snapshot (forecast made from data through {month_label(forecast_origin)})."
              if is_snapshot else ""))
scenario_columns = {"month": "Month", "record_type": "Record type"}
scenario_formats = {}
for key, measure in MEASURES.items():
    unit, number_format = {"production": ("kbd", "%.1f"), "price": ("$/bbl", "%.2f"), "value": ("$bn", "%.3f")}[key]
    for case in ("low", "base", "high"):
        name = f"{measure['label']} – {case} ({unit})"
        scenario_columns[measure["forecast"].format(case)] = name
        scenario_formats[name] = st.column_config.NumberColumn(format=number_format)
scenario_table = future[list(scenario_columns)].rename(columns=scenario_columns)
st.dataframe(scenario_table, hide_index=True, width="stretch", column_config=scenario_formats)
st.download_button(
    "Download scenario table (CSV)",
    data=scenario_table.to_csv(index=False).encode("utf-8"),
    file_name=f"{'snapshot_' if is_snapshot else ''}forecast_scenarios_from_{forecast_origin}.csv",
    mime="text/csv",
)

# --- Backtest ----------------------------------------------------------------
st.header("How accurate were these methods in the past? (backtest)")
selected = backtest[backtest["selected_for_forecast"] == "yes"]
shown_horizons = [1, 3, 6, 12]

left, right = st.columns(2)
for column, series, title in ((left, "production", "Production"), (right, "price", "WTI price")):
    rows = selected[(selected["series"] == series) & (selected["horizon_months"].isin(shown_horizons))]
    units = rows["units"].iloc[0]
    short_units = {"thousand bbl/day": "kbd"}.get(units, units)  # keeps the table narrow enough to fit
    miss_column = f"Avg miss ({short_units})"
    column.markdown(f"**{title}** · method: `{rows['method'].iloc[0]}` · units: {units}")
    column.dataframe(
        rows.rename(columns={
            "horizon_months": "Months ahead", "mape_pct": "Avg miss (%)", "mae": miss_column,
            "bias_pct": "Bias (%)", "p90_abs_pct_error": "90% of misses under (%)", "n_forecasts": "Tests",
        })[["Months ahead", "Avg miss (%)", miss_column, "Bias (%)", "90% of misses under (%)", "Tests"]],
        hide_index=True, width="stretch",
        column_config={c: st.column_config.NumberColumn(format="%.1f")
                       for c in ["Avg miss (%)", miss_column, "Bias (%)", "90% of misses under (%)"]},
    )


def error_at(series, horizon):
    row = selected[(selected["series"] == series) & (selected["horizon_months"] == horizon)]
    return row["mape_pct"].iloc[0]


st.info(
    f"**Why the price forecast is much less certain.** Looking 12 months ahead, the production forecast "
    f"missed by {error_at('production', 12):.1f}% on average in testing; the WTI price forecast missed by "
    f"{error_at('price', 12):.1f}%. U.S. production changes slowly: wells, rigs and pipelines take months "
    f"to add or remove, so the recent trend is a decent guide. Oil prices can jump in a single month on "
    f"OPEC+ decisions, wars, recessions or inventory surprises, none of which show up in past prices. "
    f"That's why the simplest price forecast (\"next month = last month\") beat the alternatives, and why "
    f"the price scenario range is so wide. In March 2026 alone, WTI rose from about \\$64 to \\$91. "
    f"Treat the price scenarios as a range of plausible outcomes, not a prediction."
)
with st.expander("All methods and horizons (full backtest)"):
    st.dataframe(backtest.fillna({"selected_for_forecast": ""}), hide_index=True, width="stretch")

# --- Sources and assumptions -------------------------------------------------
st.header("Sources, assumptions and limitations")
src, assumptions = st.columns(2)
with src:
    st.markdown("**Data source:** U.S. Energy Information Administration (EIA), Open Data API v2")
    st.markdown(
        "| Data | EIA series | Units |\n|---|---|---|\n"
        "| U.S. field production of crude oil | MCRFPUS1 / MCRFPUS2 | thousand bbl / thousand bbl per day |\n"
        "| WTI spot price, Cushing OK (FOB) | RWTC | \\$ per barrel |"
    )
    if is_snapshot:
        st.markdown(f"**Data shown:** historical snapshot `{data_dir.relative_to(PROJECT_DIR)}`  \n"
                    f"{snapshot_info['attribution']}")
    if sources:
        for s in sources:
            st.markdown(f"- `{s['file']}` downloaded **{utc_label(s['downloaded_at_utc'])}**  \n"
                        f"  <small>{s['url_without_api_key']}</small>", unsafe_allow_html=True)
    else:
        st.caption("Download details not found in data/raw/eia/. Rerun `python eia_pipeline.py` to record them.")
    st.caption("Fictional Milestone 1 well data is deliberately not shown here.")
with assumptions:
    methods = selected.drop_duplicates("series").set_index("series")["method"]
    st.markdown(
        f"- **Forecast horizon:** {HORIZON} months, from data through {month_label(forecast_origin)}.\n"
        f"- **Base production:** `{methods['production']}`: {METHODS[methods['production']].__doc__.strip()}\n"
        f"- **Base price:** `{methods['price']}`: {METHODS[methods['price']].__doc__.strip()}\n"
        f"- **Low / high:** base ∓ the {SCENARIO_PERCENTILE * 100:.0f}th percentile of the method's backtest % misses "
        f"at that horizon; the range never narrows further out.\n"
        f"- **Production value:** forecast kbd × days in month × 1,000 × forecast WTI \\$/bbl. Low pairs low "
        f"production with low price, and high pairs high with high, so it's a stress range, not a probability range.\n"
        f"- **Limitations:** {len(eia)} months of history; price ranges reflect only "
        f"{eia['month'].iloc[0][:4]}–{last_actual_month[:4]} volatility; no futures, "
        f"rig counts or EIA STEO inputs; EIA revises recent months."
    )
st.warning(NOT_REVENUE, icon="⚠️")
