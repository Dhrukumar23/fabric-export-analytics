"""Incremental pipeline: landing files -> data contracts -> star schema -> Delta tables + Parquet.

Re-runnable and idempotent:
  * a watermark records the last processed ERP/CRM business date
  * every fact row carries its source file, so re-delivered files replace their rows
  * customers use SCD Type 2 with inferred members for late-arriving CRM records
The same function runs on a laptop, in GitHub Actions and in a Microsoft Fabric
Python notebook (pass lake_tables="/lakehouse/default/Tables").
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import CHANNELS, COUNTRIES, HISTORY_START, Paths
from ..quality import CheckResult, apply_contract
from ..sources.fx import load_rates
from . import ingest
from .model import CustomerDimension, dim_channel, dim_country, dim_date, dim_product

GOLD_TABLES = ("dim_date", "dim_country", "dim_channel", "dim_product", "dim_customer", "fact_sales",
               "fact_target", "fact_fx_rate", "fact_data_quality", "rls_user_country")


@dataclass
class RunSummary:
    run_id: str
    started: str
    days_processed: list[str] = field(default_factory=list)
    rows_loaded: int = 0
    rows_quarantined: int = 0
    fx_source: str = ""
    customer_stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- io helpers

def _read_gold(paths: Paths, name: str) -> pl.DataFrame | None:
    p = paths.gold / f"{name}.parquet"
    return pl.read_parquet(p) if p.exists() else None


def _to_arrow(df: pl.DataFrame) -> pa.Table:
    t = df.to_arrow()
    fields = []
    for f in t.schema:  # plain string/int types are the most portable for Power Query and Spark
        if pa.types.is_large_string(f.type) or pa.types.is_string_view(f.type):
            fields.append(pa.field(f.name, pa.string()))
        else:
            fields.append(f)
    return t.cast(pa.schema(fields))


def write_gold(paths: Paths, tables: dict[str, pl.DataFrame]) -> None:
    for name, df in tables.items():
        pq.write_table(_to_arrow(df), paths.gold / f"{name}.parquet", compression="snappy")


def write_delta(lake_tables: Path, tables: dict[str, pl.DataFrame], new_sales: pl.DataFrame | None) -> None:
    """Write OneLake-compatible Delta tables. fact_sales is MERGEd so the lakehouse keeps history cheaply."""
    from deltalake import DeltaTable, write_deltalake

    lake_tables.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        target = str(lake_tables / name)
        if name == "fact_sales" and new_sales is not None and DeltaTable.is_deltatable(target):
            (DeltaTable(target)
             .merge(source=_to_arrow(new_sales), source_alias="s", target_alias="t",
                    predicate="t.order_id = s.order_id AND t.line_no = s.line_no")
             .when_matched_update_all().when_not_matched_insert_all().execute())
        else:
            write_deltalake(target, _to_arrow(df), mode="overwrite", schema_mode="overwrite")


# --------------------------------------------------------------------------- main

def run(root: Path, until: date | None = None, allow_network: bool = True, write_lake: bool = True,
        lake_tables: Path | None = None) -> RunSummary:
    paths = Paths(root).ensure()
    run_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    summary = RunSummary(run_id=run_id, started=now.isoformat(timespec="seconds"))
    checks: list[CheckResult] = []
    check_dates: list[date] = []

    def log(results: list[CheckResult], d: date):
        checks.extend(results)
        check_dates.extend([d] * len(results))

    state_file = paths.state / "watermark.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {"last_business_date": None, "runs": []}
    last = date.fromisoformat(state["last_business_date"]) if state["last_business_date"] else None

    # ---- product master (full snapshot every run)
    pm_file = paths.landing / "erp" / "product_master.csv"
    pm_valid, _, res = apply_contract(ingest.read_product_master(pm_file), "product_master", pm_file.name)
    run_day = until or date.today()
    log(res, run_day)
    products = dim_product(pm_valid, _read_gold(paths, "dim_product"))
    sku_set = set(products["sku"].to_list())

    # ---- which business days are new?
    erp_files = {ingest.file_date(p): p for p in (paths.landing / "erp").glob("erp_orders_*.csv")}
    crm_files = {ingest.file_date(p): p for p in (paths.landing / "crm").glob("crm_customers_changes_*.json")}
    days = sorted(d for d in set(erp_files) | set(crm_files) if (last is None or d > last) and (until is None or d <= until))

    customers = CustomerDimension.from_frame(_read_gold(paths, "dim_customer"))
    new_lines: list[pl.DataFrame] = []
    quarantined: list[pl.DataFrame] = []
    volume_history = _volume_history(_read_gold(paths, "fact_sales"))

    for d in days:
        if d in crm_files:
            raw = ingest.read_crm_changes(crm_files[d])
            valid, bad, res = apply_contract(raw, "crm_customers", crm_files[d].name)
            log(res, d)
            for rec in valid.sort("updated_at").to_dicts():
                customers.apply_change(rec)
            if bad.height:
                quarantined.append(bad.select(pl.lit("crm_customers").alias("contract"), "_file", "_reasons",
                                              pl.struct(pl.exclude("_file", "_reasons")).struct.json_encode().alias("record")))
        if d in erp_files:
            f = erp_files[d]
            valid, bad, res = apply_contract(ingest.read_erp_orders(f), "erp_order_lines", f.name, {"product.sku": sku_set})
            log(res, d)
            if bad.height:
                quarantined.append(bad.select(pl.lit("erp_order_lines").alias("contract"), "_file", "_reasons",
                                              pl.struct(pl.exclude("_file", "_reasons")).struct.json_encode().alias("record")))
            sks = [customers.sk_for(c, cc, od) for c, cc, od in
                   zip(valid["customer_id"], valid["ship_to_country"], valid["order_date"])]
            valid = valid.with_columns(pl.Series("customer_sk", sks, dtype=pl.Int32), pl.lit(f.name).alias("source_file"))
            new_lines.append(valid)
            # volume anomaly vs the previous 20 business days
            hist = volume_history[-20:]
            if len(hist) >= 10:
                med = sorted(hist)[len(hist) // 2]
                ratio = valid.height / med if med else 1.0
                anomalous = ratio < 0.4 or ratio > 2.5
                log([CheckResult("erp_order_lines", f.name, "volume vs 20-day median", valid.height,
                                 valid.height if anomalous else 0, "warning" if anomalous else "info",
                                 f"ratio {ratio:.2f}" if anomalous else "passed")], d)
            volume_history.append(valid.height)
        summary.days_processed.append(d.isoformat())

    customer_stats = dict(customers.stats)
    for k in ("inferred", "inferred_resolved"):
        if customer_stats.get(k):
            log([CheckResult("crm_customers", "-", f"late-arriving customers: {k.replace('_', ' ')}",
                             customer_stats[k], customer_stats[k] if k == "inferred" else 0,
                             "warning" if k == "inferred" else "info",
                             "inferred member created" if k == "inferred" else "placeholder completed")], run_day)
    summary.customer_stats = customer_stats

    last_actual = max(days) if days else (last or HISTORY_START)

    # ---- FX: ECB live (fallback: synthetic) -> contract
    rates_raw, fx_source = load_rates(date(HISTORY_START.year - 1, 10, 1), last_actual, paths.landing, allow_network)
    rates, _, res = apply_contract(rates_raw, "fx_rates", fx_source)
    log(res, run_day)
    fx_age = (last_actual - rates["rate_date"].max()).days
    log([CheckResult("fx_rates", fx_source, "freshness (max 5 days)", rates.height, int(fx_age > 5),
                     "warning" if (fx_age > 5 or "fallback" in fx_source.lower()) else "info",
                     fx_source)], run_day)
    summary.fx_source = fx_source
    planning = _planning_rates(rates)

    # ---- facts
    prev_sales = _read_gold(paths, "fact_sales")
    new_sales = _build_sales(new_lines, products, rates, planning) if new_lines else None
    if new_sales is not None:
        missing_fx = int(new_sales["fx_rate"].is_null().sum())
        log([CheckResult("fact_sales", "-", "fx rate found for every line", new_sales.height, missing_fx,
                         "error" if missing_fx else "info", "passed" if not missing_fx else "check fx feed")], run_day)
    if prev_sales is not None and new_sales is not None:
        files = new_sales["source_file"].unique().to_list()
        fact_sales = pl.concat([prev_sales.filter(~pl.col("source_file").is_in(files)), new_sales], how="vertical_relaxed")
    else:
        fact_sales = new_sales if new_sales is not None else prev_sales
    fact_sales = fact_sales.sort(["order_date", "order_id", "line_no"])

    fact_target = _build_targets(paths, log, run_day)
    fact_fx = rates.select(pl.col("rate_date").alias("date"),
                           (pl.col("rate_date").dt.year() * 10000 + pl.col("rate_date").dt.month() * 100
                            + pl.col("rate_date").dt.day()).cast(pl.Int32).alias("date_key"),
                           "currency", "rate_per_eur").filter(pl.col("date") >= HISTORY_START)

    # ---- data-quality log (append) and quarantine
    dq_new = pl.DataFrame([c.to_dict() for c in checks]).with_columns(
        pl.Series("check_date", check_dates, dtype=pl.Date),
        pl.lit(run_id).alias("run_id"),
        pl.lit(now.replace(tzinfo=None)).alias("run_timestamp"),
    ).with_columns(
        (pl.col("check_date").dt.year() * 10000 + pl.col("check_date").dt.month() * 100
         + pl.col("check_date").dt.day()).cast(pl.Int32).alias("date_key"),
        pl.col("rows_checked").cast(pl.Int64), pl.col("rows_failed").cast(pl.Int64))
    prev_dq = _read_gold(paths, "fact_data_quality")
    fact_dq = dq_new if prev_dq is None else pl.concat([prev_dq, dq_new], how="vertical_relaxed")
    if quarantined:
        q_new = pl.concat(quarantined).rename({"_file": "file", "_reasons": "reasons"}).with_columns(pl.lit(run_id).alias("run_id"))
        q_path = paths.quarantine / "quarantine.parquet"
        q_all = q_new if not q_path.exists() else pl.concat([pl.read_parquet(q_path), q_new])
        pq.write_table(_to_arrow(q_all), q_path, compression="snappy")
        summary.rows_quarantined = q_new.height

    tables = {
        "dim_date": dim_date(HISTORY_START, date(last_actual.year + 1, 12, 31), last_actual),
        "dim_country": dim_country(),
        "dim_channel": dim_channel(),
        "dim_product": products,
        "dim_customer": customers.to_frame(),
        "fact_sales": fact_sales,
        "fact_target": fact_target,
        "fact_fx_rate": fact_fx,
        "fact_data_quality": fact_dq,
        "rls_user_country": _rls_mapping(),
    }
    write_gold(paths, tables)
    if write_lake:
        write_delta(lake_tables or paths.lake_tables, tables, new_sales)

    summary.rows_loaded = 0 if new_sales is None else new_sales.height
    summary.warnings = [f"{c.contract}: {c.check} ({c.rows_failed})" for c in checks if c.severity in ("warning", "error") and c.rows_failed]
    state["last_business_date"] = last_actual.isoformat()
    state["runs"] = (state.get("runs", []) + [{
        "run_id": run_id, "started": summary.started, "days": len(days), "rows_loaded": summary.rows_loaded,
        "rows_quarantined": summary.rows_quarantined, "fx_source": fx_source}])[-60:]
    state_file.write_text(json.dumps(state, indent=2))
    return summary


# --------------------------------------------------------------------------- builders

def _volume_history(prev_sales: pl.DataFrame | None) -> list[int]:
    if prev_sales is None:
        return []
    return prev_sales.group_by("source_file").len().sort("source_file")["len"].to_list()


def _build_sales(new_lines: list[pl.DataFrame], products: pl.DataFrame, rates: pl.DataFrame,
                 planning: pl.DataFrame) -> pl.DataFrame:
    lines = pl.concat(new_lines)
    channel_key = {c: i + 1 for i, c in enumerate(CHANNELS)}
    df = (lines
          .join(products.select("sku", "product_sk", "unit_cost_eur"), on="sku", how="left")
          .with_columns(pl.col("channel").replace_strict(channel_key, return_dtype=pl.Int32).alias("channel_key"))
          .sort("order_date"))
    # as-of join: ECB has no rates on weekends/holidays -> use the latest published rate
    fx = rates.select("currency", pl.col("rate_date").alias("order_date"), "rate_per_eur").sort("order_date")
    df = df.join_asof(fx, on="order_date", by="currency", strategy="backward", check_sortedness=False)
    df = df.with_columns(pl.when(pl.col("currency") == "EUR").then(1.0).otherwise(pl.col("rate_per_eur")).alias("fx_rate"))
    df = df.with_columns(pl.col("order_date").dt.year().cast(pl.Int32).alias("year")).join(
        planning, on=["year", "currency"], how="left")
    gross = pl.col("quantity") * pl.col("unit_price_local")
    disc = gross * pl.col("discount_pct") / 100
    df = df.with_columns(
        gross.round(2).alias("gross_local"),
        (gross - disc).round(2).alias("net_local"),
        (gross / pl.col("fx_rate")).round(2).alias("gross_eur"),
        (disc / pl.col("fx_rate")).round(2).alias("discount_eur"),
        ((gross - disc) / pl.col("fx_rate")).round(2).alias("net_eur"),
        ((gross - disc) / pl.col("planning_rate")).round(2).alias("net_eur_plan_fx"),
        (pl.col("quantity") * pl.col("unit_cost_eur")).round(2).alias("cost_eur"),
        (pl.col("status") == "Retoure").alias("is_return"),
        (pl.col("order_date").dt.year() * 10000 + pl.col("order_date").dt.month() * 100
         + pl.col("order_date").dt.day()).cast(pl.Int32).alias("date_key"),
    )
    return df.select(
        "order_id", "line_no", "order_date", "date_key", "customer_sk", "product_sk",
        pl.col("ship_to_country").alias("country_code"), "channel_key", "status", "is_return",
        "quantity", "unit_price_local", "discount_pct", "currency", "fx_rate", "planning_rate",
        "gross_local", "net_local", "gross_eur", "discount_eur", "net_eur", "net_eur_plan_fx", "cost_eur",
        "source_file")


def _build_targets(paths: Paths, log, run_day: date) -> pl.DataFrame:
    name_to_code = {c.name: c.code for c in COUNTRIES}
    frames = []
    for f in sorted((paths.landing / "controlling").glob("budget_umsatz_*.csv")):
        valid, bad, res = apply_contract(ingest.read_budget(f), "sales_budget", f.name)
        log(res, run_day)
        frames.append(valid)
    df = pl.concat(frames)
    return df.select(
        pl.col("country_name").replace_strict(name_to_code).alias("country_code"),
        "month_start",
        (pl.col("month_start").dt.year() * 10000 + pl.col("month_start").dt.month() * 100 + 1).cast(pl.Int32).alias("date_key"),
        (pl.col("target_eur") * 1000).round(0).alias("target_eur"),
    ).filter(pl.col("target_eur") > 0).sort(["month_start", "country_code"])


def _planning_rates(rates: pl.DataFrame) -> pl.DataFrame:
    """Treasury convention: the planning rate of a year is the Q4 average of the previous year."""
    q4 = (rates.filter(pl.col("rate_date").dt.month() >= 10)
          .group_by(pl.col("rate_date").dt.year().alias("prev"), "currency")
          .agg(pl.col("rate_per_eur").mean().round(4).alias("planning_rate"))
          .select((pl.col("prev") + 1).cast(pl.Int32).alias("year"), "currency", "planning_rate"))
    years = q4["year"].unique().to_list()
    eur = pl.DataFrame({"year": years, "currency": ["EUR"] * len(years), "planning_rate": [1.0] * len(years)},
                       schema={"year": pl.Int32, "currency": pl.String, "planning_rate": pl.Float64})
    return pl.concat([q4, eur])


def _rls_mapping() -> pl.DataFrame:
    """Demo security mapping for the 'Country Manager' role (user principal -> countries)."""
    rows = [("manager.dach@valdora.example", "DE"), ("manager.dach@valdora.example", "CH"),
            ("manager.west@valdora.example", "FR"), ("manager.west@valdora.example", "IT"),
            ("manager.west@valdora.example", "GB"), ("manager.cne@valdora.example", "PL"),
            ("manager.cne@valdora.example", "SE"), ("manager.na@valdora.example", "US"),
            ("manager.apac@valdora.example", "JP")]
    return pl.DataFrame(rows, schema={"user_principal": pl.String, "country_code": pl.String}, orient="row")
