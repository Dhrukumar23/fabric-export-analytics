"""Builds docs/index.html: a static, self-contained web dashboard published by GitHub Pages.

It mirrors the Power BI report's headline pages so the project can be shown
to anyone with a browser, and it is rebuilt by the same daily workflow.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

from .config import COMPANY, Paths


def _aggregates(paths: Paths) -> dict:
    g = paths.gold
    f = pl.read_parquet(g / "fact_sales.parquet")
    t = pl.read_parquet(g / "fact_target.parquet")
    cust = pl.read_parquet(g / "dim_customer.parquet").select("customer_sk", "segment")
    ctry = pl.read_parquet(g / "dim_country.parquet")
    dq = pl.read_parquet(g / "fact_data_quality.parquet")
    last: date = f["order_date"].max()

    f = f.with_columns(pl.col("order_date").dt.year().alias("year"), pl.col("order_date").dt.month_start().alias("month"))
    monthly = (f.group_by("month").agg(pl.col("net_eur").sum().alias("actual"), (pl.col("net_eur") - pl.col("cost_eur")).sum().alias("gm"))
               .join(t.group_by("month_start").agg(pl.col("target_eur").sum().alias("target")), left_on="month", right_on="month_start", how="full", coalesce=True)
               .filter(pl.col("month") <= last).sort("month").fill_null(0))

    years = sorted(f["year"].unique().to_list())
    per_year = {}
    for y in years:
        fy = f.filter(pl.col("year") == y)
        # compare like with like: in the running year, only months up to the last loaded month, current month pro-rata by days
        ty = t.filter(pl.col("month_start").dt.year() == y)
        if y == last.year:
            import calendar
            dim = calendar.monthrange(y, last.month)[1]
            ty = ty.filter(pl.col("month_start").dt.month() <= last.month).with_columns(
                pl.when(pl.col("month_start").dt.month() == last.month).then(pl.col("target_eur") * last.day / dim)
                  .otherwise(pl.col("target_eur")).alias("target_eur"))
        by_c = (ctry.select("country_code", "country", "currency", "country_sort")
                .join(fy.group_by("country_code").agg(pl.col("net_eur").sum().alias("actual"),
                                                       pl.col("net_eur_plan_fx").sum().alias("plan_fx")), on="country_code", how="left")
                .join(ty.group_by("country_code").agg(pl.col("target_eur").sum().alias("target")), on="country_code", how="left")
                .fill_null(0).filter(pl.col("target") > 0).sort("country_sort"))
        seg = (fy.join(cust, on="customer_sk").group_by("segment")
               .agg(pl.col("net_eur").sum().alias("revenue"), (pl.col("net_eur") - pl.col("cost_eur")).sum().alias("gm"),
                    pl.col("discount_eur").sum().alias("disc"), pl.col("gross_eur").sum().alias("gross"))
               .with_columns((pl.col("gm") / pl.col("revenue")).alias("gm_pct"), (pl.col("disc") / pl.col("gross")).alias("disc_pct"))
               .sort("gm_pct"))
        fx = (fy.filter(pl.col("currency") != "EUR").group_by("currency")
              .agg((pl.col("net_eur") - pl.col("net_eur_plan_fx")).sum().alias("effect")).sort("effect"))
        per_year[str(y)] = {
            "revenue": float(fy["net_eur"].sum()), "target": float(by_c["target"].sum()),
            "gm_pct": float((fy["net_eur"].sum() - fy["cost_eur"].sum()) / fy["net_eur"].sum()),
            "fx_effect": float(fx["effect"].sum()) if fx.height else 0.0,
            "orders": int(fy["order_id"].n_unique()),
            "countries": by_c.select("country", "actual", "target").to_dicts(),
            "segments": seg.select("segment", "revenue", "gm_pct", "disc_pct").to_dicts(),
            "fx": fx.to_dicts(),
            "partial": y == last.year,
        }

    dq_daily = (dq.filter(pl.col("contract") == "erp_order_lines")
                .group_by("check_date").agg(
                    pl.col("rows_failed").filter(pl.col("action") == "quarantined").sum().alias("quarantined"),
                    pl.col("rows_failed").filter(pl.col("action") == "auto-fixed").sum().alias("fixed"),
                    pl.col("rows_failed").filter(pl.col("action") == "deduplicated").sum().alias("dedup"),
                    pl.col("rows_checked").max().alias("rows"))
                .sort("check_date").tail(60))
    return {
        "company": COMPANY,
        "last_date": last.isoformat(),
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "years": [str(y) for y in years],
        "monthly": [{"m": r["month"].strftime("%Y-%m"), "a": round(r["actual"]), "t": round(r["target"])}
                    for r in monthly.tail(24).to_dicts()],
        "per_year": per_year,
        "dq": [{"d": r["check_date"].isoformat(), "q": int(r["quarantined"]), "f": int(r["fixed"]), "u": int(r["dedup"]), "n": int(r["rows"])}
               for r in dq_daily.to_dicts()],
        "fact_rows": f.height,
        "fx_source": next((r["file"] for r in dq.filter(pl.col("contract") == "fx_rates").tail(3).to_dicts()), ""),
    }


def build_dashboard(paths: Paths, out: Path) -> Path:
    data = _aggregates(paths)
    template = (Path(__file__).parent / "dashboard_template.html").read_text(encoding="utf-8")
    html = template.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
