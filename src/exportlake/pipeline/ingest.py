"""Readers that turn landing-zone files into raw (all-text) frames. No business logic here."""
from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ..generator.exports import MONTHS_DE

FILE_DATE = re.compile(r"(\d{8})")


def file_date(path: Path) -> date:
    m = FILE_DATE.search(path.name)
    if not m:
        raise ValueError(f"no date in file name {path.name}")
    s = m.group(1)
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def read_delimited(path: Path, encoding: str = "cp1252", sep: str = ";") -> pl.DataFrame:
    text = path.read_bytes().decode(encoding)
    return pl.read_csv(io.BytesIO(text.encode("utf-8")), separator=sep, infer_schema_length=0)


def read_erp_orders(path: Path) -> pl.DataFrame:
    return read_delimited(path)


def read_product_master(path: Path) -> pl.DataFrame:
    return read_delimited(path)


def read_crm_changes(path: Path) -> pl.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for it in payload.get("data", []):
        rows.append({
            "customer_id": it.get("id"),
            "customer_name": (it.get("company") or {}).get("name"),
            "country_code": (it.get("company") or {}).get("legalCountry"),
            "segment": (it.get("classification") or {}).get("segment"),
            "is_key_account": (it.get("classification") or {}).get("keyAccount"),
            "sales_rep": (it.get("owner") or {}).get("fullName"),
            "created_at": (it.get("createdAt") or "")[:10],
            "updated_at": (it.get("updatedAt") or "")[:10],
        })
    schema = {"customer_id": pl.String, "customer_name": pl.String, "country_code": pl.String, "segment": pl.String,
              "is_key_account": pl.Boolean, "sales_rep": pl.String, "created_at": pl.String, "updated_at": pl.String}
    return pl.DataFrame(rows, schema=schema)


def read_budget(path: Path) -> pl.DataFrame:
    """Wide budget (Land; Jan..Dez) -> long (country_name, month_start, target_eur as text, EUR thousands)."""
    year = int(re.search(r"(\d{4})", path.name).group(1))
    wide = read_delimited(path)
    long = wide.unpivot(index="Land", variable_name="month", value_name="target_eur")
    month_no = {m: i + 1 for i, m in enumerate(MONTHS_DE)}
    return long.select(
        pl.col("Land").alias("country_name"),
        pl.col("month").replace_strict(month_no, return_dtype=pl.Int32)
          .map_elements(lambda m: date(year, m, 1).isoformat(), return_dtype=pl.String).alias("month_start"),
        pl.col("target_eur"),
    )
