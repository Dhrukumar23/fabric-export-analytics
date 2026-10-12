"""Data-contract engine.

Each source has a YAML contract (src/exportlake/contracts). `apply_contract`
renames, cleanses and type-casts a raw frame, then splits it into
  - valid rows   -> continue to the model
  - quarantined  -> kept with the reason, never silently dropped
and returns one CheckResult per rule for the data-quality log that Power BI shows.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import resources

import polars as pl
import yaml


@dataclass
class CheckResult:
    contract: str
    file: str
    check: str
    rows_checked: int
    rows_failed: int
    severity: str   # error = row quarantined, warning = fixed/flagged, info
    action: str

    def to_dict(self) -> dict:
        return asdict(self)


@lru_cache(maxsize=None)
def load_contract(name: str) -> dict:
    text = resources.files("exportlake.contracts").joinpath(f"{name}.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _de_decimal(col: pl.Expr) -> pl.Expr:
    return col.str.strip_chars().str.replace_all(r"\.", "").str.replace(",", ".").cast(pl.Float64, strict=False)


def _cast(df: pl.DataFrame, name: str, spec: dict, number_format: str | None, date_format: str | None) -> pl.Expr:
    c = pl.col(name)
    t = spec["type"]
    is_text = df.schema[name] == pl.String
    if t == "string":
        return c.cast(pl.String)
    if t == "int":
        return (c.str.strip_chars().cast(pl.Int64, strict=False) if is_text else c.cast(pl.Int64, strict=False))
    if t == "decimal":
        if is_text:
            return _de_decimal(c) if number_format == "de-DE" else c.cast(pl.Float64, strict=False)
        return c.cast(pl.Float64, strict=False)
    if t == "date":
        if is_text:
            return c.str.strip_chars().str.to_date(date_format or "%Y-%m-%d", strict=False)
        return c.cast(pl.Date, strict=False)
    if t == "bool":
        return c.cast(pl.Boolean, strict=False)
    raise ValueError(f"unknown type {t}")


def apply_contract(raw: pl.DataFrame, name: str, file: str, references: dict[str, set] | None = None
                   ) -> tuple[pl.DataFrame, pl.DataFrame, list[CheckResult]]:
    ct = load_contract(name)
    src = ct.get("source", {})
    results: list[CheckResult] = []
    n = raw.height
    df = raw.rename({k: v for k, v in (ct.get("rename") or {}).items() if k in raw.columns})
    cols = ct["columns"]

    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: schema drift, missing columns {missing} in {file}")
    extra = [c for c in df.columns if c not in cols]
    if extra:
        results.append(CheckResult(name, file, "schema: unexpected columns", n, len(extra), "warning", "ignored"))
    df = df.select(list(cols))

    # 1) cleansing that can safely repair values (counted, not quarantined)
    for c, spec in cols.items():
        for op in spec.get("cleanse", []):
            before = df[c]
            if op == "trim":
                df = df.with_columns(pl.col(c).str.strip_chars())
            elif op == "upper":
                df = df.with_columns(pl.col(c).str.to_uppercase())
            fixed = int((before != df[c]).fill_null(False).sum())
            if fixed:
                results.append(CheckResult(name, file, f"{c}: {op}", n, fixed, "warning", "auto-fixed"))

    # 2) type casting
    df = df.with_columns([_cast(df, c, spec, src.get("numberFormat"), src.get("dateFormat")).alias(c)
                          for c, spec in cols.items()])

    # 3) row-level rules -> failure reasons
    reasons: list[pl.Expr] = []

    def rule(check: str, failed: pl.Expr):
        cnt = int(df.select(failed.fill_null(True).sum()).item())
        results.append(CheckResult(name, file, check, n, cnt, "error" if cnt else "info", "quarantined" if cnt else "passed"))
        reasons.append(pl.when(failed.fill_null(True)).then(pl.lit(check)).otherwise(None))

    for c, spec in cols.items():
        col = pl.col(c)
        if spec.get("required"):
            empty = col.is_null() | ((col == "") if spec["type"] == "string" else pl.lit(False))
            rule(f"{c}: required", empty)
        if "pattern" in spec:
            rule(f"{c}: format", col.is_not_null() & ~col.str.contains(spec["pattern"]) & (col != ""))
        if "allowed" in spec:
            rule(f"{c}: allowed values", col.is_not_null() & ~col.is_in(spec["allowed"]))
        if "min" in spec:
            rule(f"{c}: >= {spec['min']}", col.is_not_null() & (col < spec["min"]))
        if "max" in spec:
            rule(f"{c}: <= {spec['max']}", col.is_not_null() & (col > spec["max"]))
        if "notEqual" in spec:
            rule(f"{c}: <> {spec['notEqual']}", col.is_not_null() & (col == spec["notEqual"]))
        if "references" in spec and references is not None:
            keys = references.get(spec["references"], set())
            rule(f"{c}: exists in {spec['references']}", col.is_not_null() & (col != "") & ~col.is_in(list(keys)))
    for r in ct.get("rules", []):
        rule(r["name"], ~pl.sql_expr(r["expr"]))

    flagged = df.with_columns(pl.concat_list(reasons).list.drop_nulls().list.join("; ").alias("_reasons"))
    quarantine = flagged.filter(pl.col("_reasons") != "").with_columns(pl.lit(file).alias("_file"))
    valid = flagged.filter(pl.col("_reasons") == "").drop("_reasons")

    # 4) primary key / duplicates
    pk = ct["primaryKey"]
    dups = valid.height - valid.unique(subset=pk).height
    policy = ct.get("onDuplicate", "keep_first")
    if dups and policy == "fail":
        raise ValueError(f"{name}: {dups} duplicate keys in {file}")
    keep = "last" if policy == "keep_last" else "first"
    valid = valid.unique(subset=pk, keep=keep, maintain_order=True)
    results.append(CheckResult(name, file, f"unique key ({', '.join(pk)})", n, dups,
                               "warning" if dups else "info", "deduplicated" if dups else "passed"))
    return valid, quarantine, results
