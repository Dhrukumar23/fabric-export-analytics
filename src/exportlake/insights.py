"""Automated daily KPI digest: what changed, what is off-target, and what looks unusual.

Written to reports/daily_digest.md and to the GitHub Actions run summary, so the
team gets the commentary without opening a report.
"""
from __future__ import annotations

import json
from datetime import date

import polars as pl

from .config import Paths


def _eur(x: float) -> str:
    return f"€{x/1e6:,.2f}M" if abs(x) >= 1e6 else f"€{x/1e3:,.0f}k"


def compute(paths: Paths) -> dict:
    g = paths.gold
    f = pl.read_parquet(g / "fact_sales.parquet")
    t = pl.read_parquet(g / "fact_target.parquet")
    c = pl.read_parquet(g / "dim_customer.parquet").select("customer_sk", "segment")
    ctry = pl.read_parquet(g / "dim_country.parquet")
    dq = pl.read_parquet(g / "fact_data_quality.parquet")
    dd = pl.read_parquet(g / "dim_date.parquet")

    last: date = f["order_date"].max()
    y, m = last.year, last.month
    daily = f.group_by("order_date").agg(pl.col("net_eur").sum().alias("rev")).sort("order_date")
    last_bd: date = daily.filter(pl.col("order_date").dt.weekday() <= 5)["order_date"].max()
    same_wd = daily.filter((pl.col("order_date").dt.weekday() == last_bd.isoweekday()) & (pl.col("order_date") < last_bd)).tail(8)
    last_rev = float(daily.filter(pl.col("order_date") == last_bd)["rev"].sum())
    base = float(same_wd["rev"].mean() or 0)

    # pro-rata target by working days elapsed in the month
    wd = dd.filter((pl.col("year") == y) & (pl.col("month_no") == m) & pl.col("is_working_day"))
    share = wd.filter(pl.col("date") <= last).height / max(1, wd.height)
    mtd = (f.filter((pl.col("order_date").dt.year() == y) & (pl.col("order_date").dt.month() == m))
             .group_by("country_code").agg(pl.col("net_eur").sum().alias("mtd")))
    mt = t.filter((pl.col("month_start").dt.year() == y) & (pl.col("month_start").dt.month() == m)).select("country_code", "target_eur")
    ytd = (f.filter(pl.col("order_date").dt.year() == y).group_by("country_code")
             .agg(pl.col("net_eur").sum().alias("ytd"), pl.col("net_eur_plan_fx").sum().alias("ytd_plan_fx")))
    yt = (t.filter((pl.col("month_start").dt.year() == y) & (pl.col("month_start").dt.month() < m))
            .group_by("country_code").agg(pl.col("target_eur").sum().alias("ytd_target_closed")))
    by_c = (ctry.select("country_code", "country").join(mtd, on="country_code", how="left").join(mt, on="country_code", how="left")
            .join(ytd, on="country_code", how="left").join(yt, on="country_code", how="left").fill_null(0)
            .with_columns((pl.col("ytd_target_closed") + pl.col("target_eur") * share).alias("ytd_target"))
            .with_columns(
                pl.when(pl.col("target_eur") > 0).then(pl.col("mtd") / (pl.col("target_eur") * share)).otherwise(None).alias("mtd_pace"),
                pl.when(pl.col("ytd_target") > 0).then(pl.col("ytd") / pl.col("ytd_target")).otherwise(None).alias("ytd_ach"),
                (pl.col("ytd") - pl.col("ytd_plan_fx")).alias("fx_impact")))

    # anomaly detection: robust z-score of the latest day per country (median / MAD of last 30 business days)
    anomalies = []
    per = f.group_by("country_code", "order_date").agg(pl.col("net_eur").sum().alias("rev"))
    for code in by_c["country_code"]:
        s = per.filter((pl.col("country_code") == code) & (pl.col("order_date").dt.weekday() < 6)).sort("order_date").tail(31)
        if s.height < 15 or s["order_date"].max() != last:
            continue
        hist, today = s["rev"][:-1], float(s["rev"][-1])
        med = float(hist.median())
        mad = float((hist - med).abs().median()) or 1.0
        z = 0.6745 * (today - med) / mad
        if abs(z) > 3.5:
            anomalies.append({"country_code": code, "revenue": today, "median": med, "z": round(z, 1)})

    # margin drift by segment: last 28 days vs the 84 days before
    fs = f.join(c, on="customer_sk").with_columns(((pl.lit(last) - pl.col("order_date")).dt.total_days()).alias("age"))
    def gm(frame):
        return frame.group_by("segment").agg(((pl.col("net_eur").sum() - pl.col("cost_eur").sum()) / pl.col("net_eur").sum()).alias("gm"))
    drift = (gm(fs.filter(pl.col("age") < 28)).join(gm(fs.filter((pl.col("age") >= 28) & (pl.col("age") < 112))), on="segment", suffix="_prev")
             .with_columns(((pl.col("gm") - pl.col("gm_prev")) * 100).alias("pp")).sort("pp"))

    last_run = dq["run_id"][-1]
    run_dq = dq.filter(pl.col("run_id") == last_run)
    return {
        "last_business_date": last_bd.isoformat(),
        "last_data_date": last.isoformat(),
        "last_day_revenue": last_rev, "same_weekday_avg": base,
        "month_share_elapsed": share,
        "countries": by_c.sort("ytd_ach", nulls_last=True).to_dicts(),
        "anomalies": anomalies,
        "margin_drift": drift.to_dicts(),
        "dq": {"run_id": last_run, "checks": run_dq.height,
               "errors": int(run_dq.filter(pl.col("severity") == "error")["rows_failed"].sum()),
               "auto_fixed": int(run_dq.filter(pl.col("action") == "auto-fixed")["rows_failed"].sum()),
               "fx_source": next((r["file"] for r in run_dq.filter(pl.col("contract") == "fx_rates").to_dicts()), "")},
        "ytd": {"revenue": float(by_c["ytd"].sum()), "target": float(by_c["ytd_target"].sum()),
                "fx_impact": float(by_c["fx_impact"].sum())},
    }


def write_digest(paths: Paths) -> str:
    k = compute(paths)
    out_dir = paths.root / "reports"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "latest_metrics.json").write_text(json.dumps(k, indent=2, default=str))
    ytd = k["ytd"]
    lines = [
        f"# Daily sales digest · data up to {k['last_data_date']}",
        "",
        f"**Last business day ({k['last_business_date']}):** {_eur(k['last_day_revenue'])} net revenue "
        f"({(k['last_day_revenue']/k['same_weekday_avg']-1)*100:+.0f}% vs the average of the last 8 same weekdays)."
        if k["same_weekday_avg"] else "",
        f"**Year to date:** {_eur(ytd['revenue'])} against a pro-rata target of {_eur(ytd['target'])} "
        f"(**{ytd['revenue']/ytd['target']*100:.1f}%**). Currency effect vs planning rates: {_eur(ytd['fx_impact'])}.",
        "",
        "## Target achievement by country (YTD, pro-rata)",
        "",
        "| Country | YTD revenue | Achievement | Month pace | FX effect |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in k["countries"]:
        ach = f"{r['ytd_ach']*100:.0f}%" if r["ytd_ach"] is not None else "–"
        pace = f"{r['mtd_pace']*100:.0f}%" if r["mtd_pace"] is not None else "–"
        flag = " ⚠️" if r["ytd_ach"] is not None and r["ytd_ach"] < 0.9 else ""
        lines.append(f"| {r['country']}{flag} | {_eur(r['ytd'])} | {ach} | {pace} | {_eur(r['fx_impact'])} |")
    lines += ["", "## Unusual activity", ""]
    if k["anomalies"]:
        for a in k["anomalies"]:
            lines.append(f"- **{a['country_code']}**: {_eur(a['revenue'])} yesterday vs a typical {_eur(a['median'])} (robust z = {a['z']}).")
    else:
        lines.append("- No country outside its normal daily range (robust z-score, 30 business days).")
    worst = k["margin_drift"][0] if k["margin_drift"] else None
    if worst and worst["pp"] < -1.5:
        lines.append(f"- Gross margin for **{worst['segment']}** fell {abs(worst['pp']):.1f} pp in the last 4 weeks.")
    dq = k["dq"]
    lines += ["", "## Data quality (this run)", "",
              f"- {dq['checks']} contract checks · {dq['errors']} rows quarantined · {dq['auto_fixed']} values auto-fixed",
              f"- FX source: {dq['fx_source']}", ""]
    md = "\n".join(lines)
    (out_dir / "daily_digest.md").write_text(md, encoding="utf-8")
    return md
