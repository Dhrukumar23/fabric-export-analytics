"""Star-schema building blocks: conformed dimensions and the SCD Type 2 customer dimension."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import polars as pl

from ..config import CHANNELS, COUNTRIES

OPEN_END = date(9999, 12, 31)
BEGINNING = date(1900, 1, 1)
SCD2_TRACKED = ("segment", "is_key_account", "sales_rep")


def dim_date(start: date, end: date, last_actual: date) -> pl.DataFrame:
    df = pl.DataFrame({"date": pl.date_range(start, end, "1d", eager=True)})
    return df.with_columns(
        (pl.col("date").dt.year() * 10000 + pl.col("date").dt.month().cast(pl.Int32) * 100 + pl.col("date").dt.day().cast(pl.Int32)).cast(pl.Int32).alias("date_key"),
        pl.col("date").dt.year().cast(pl.Int32).alias("year"),
        pl.col("date").dt.quarter().cast(pl.Int32).alias("quarter_no"),
        ("Q" + pl.col("date").dt.quarter().cast(pl.String)).alias("quarter"),
        (pl.col("date").dt.year().cast(pl.String) + "-Q" + pl.col("date").dt.quarter().cast(pl.String)).alias("year_quarter"),
        pl.col("date").dt.month().cast(pl.Int32).alias("month_no"),
        pl.col("date").dt.strftime("%B").alias("month_name"),
        pl.col("date").dt.strftime("%b").alias("month_short"),
        pl.col("date").dt.strftime("%Y-%m").alias("year_month"),
        (pl.col("date").dt.year() * 12 + pl.col("date").dt.month()).cast(pl.Int32).alias("year_month_no"),
        pl.col("date").dt.month_start().alias("month_start"),
        pl.col("date").dt.week().cast(pl.Int32).alias("iso_week"),
        pl.col("date").dt.weekday().cast(pl.Int32).alias("weekday_no"),
        pl.col("date").dt.strftime("%a").alias("weekday"),
        pl.col("date").dt.day().cast(pl.Int32).alias("day_of_month"),
        (pl.col("date").dt.weekday() < 6).alias("is_working_day"),
        (pl.col("date") <= last_actual).alias("has_actuals"),
    )


def dim_country() -> pl.DataFrame:
    return pl.DataFrame([{
        "country_code": c.code, "country": c.name, "region": c.region, "currency": c.currency,
        "market_entry_date": c.market_entry, "country_sort": i + 1,
    } for i, c in enumerate(COUNTRIES)])


def dim_channel() -> pl.DataFrame:
    return pl.DataFrame({"channel_key": list(range(1, len(CHANNELS) + 1)), "channel": list(CHANNELS)},
                        schema={"channel_key": pl.Int32, "channel": pl.String})


def dim_product(valid: pl.DataFrame, previous: pl.DataFrame | None) -> pl.DataFrame:
    """Type 1 product dimension with stable surrogate keys across runs."""
    known = {} if previous is None else dict(zip(previous["sku"], previous["product_sk"]))
    next_sk = max(known.values(), default=0) + 1
    sks = []
    for sku in valid["sku"]:
        if sku not in known:
            known[sku] = next_sk
            next_sk += 1
        sks.append(known[sku])
    return valid.with_columns(pl.Series("product_sk", sks, dtype=pl.Int32)).select(
        "product_sk", "sku", "product_name", "category", "subcategory", "list_price_eur", "unit_cost_eur")


@dataclass
class CustomerDimension:
    """In-memory SCD Type 2 dimension with inferred-member support.

    - A tracked attribute change closes the current version and opens a new one.
    - An order for a customer the CRM has not delivered yet creates an *inferred member*
      (placeholder row). When the CRM record arrives, the placeholder is completed in place,
      so facts already loaded keep pointing at the right surrogate key.
    """
    rows: list[dict] = field(default_factory=list)
    current: dict[str, int] = field(default_factory=dict)   # customer_id -> index into rows
    next_sk: int = 1
    stats: dict[str, int] = field(default_factory=lambda: {"new": 0, "changed": 0, "inferred": 0, "inferred_resolved": 0, "unchanged": 0})

    @classmethod
    def from_frame(cls, df: pl.DataFrame | None) -> "CustomerDimension":
        dim = cls()
        if df is None or df.height == 0:
            return dim
        dim.rows = df.sort("customer_sk").to_dicts()
        for i, r in enumerate(dim.rows):
            if r["is_current"]:
                dim.current[r["customer_id"]] = i
        dim.next_sk = max(r["customer_sk"] for r in dim.rows) + 1
        return dim

    def apply_change(self, rec: dict) -> None:
        cid = rec["customer_id"]
        idx = self.current.get(cid)
        if idx is None:
            self._insert(rec, valid_from=BEGINNING, version=1)
            self.stats["new"] += 1
            return
        cur = self.rows[idx]
        if cur["is_inferred"]:
            cur.update({k: rec[k] for k in ("customer_name", "country_code", "segment", "is_key_account", "sales_rep")})
            cur["customer_since"] = rec["created_at"]
            cur["is_inferred"] = False
            self.stats["inferred_resolved"] += 1
            return
        cur["customer_name"] = rec["customer_name"]          # type 1 attribute
        if all(cur[k] == rec[k] for k in SCD2_TRACKED):
            self.stats["unchanged"] += 1
            return
        if rec["updated_at"] <= cur["valid_from"]:             # replayed / out-of-order change: ignore
            self.stats["unchanged"] += 1
            return
        cur["valid_to"] = rec["updated_at"] - timedelta(days=1)
        cur["is_current"] = False
        self._insert(rec, valid_from=rec["updated_at"], version=cur["version"] + 1, since=cur["customer_since"])
        self.stats["changed"] += 1

    def _insert(self, rec: dict, valid_from: date, version: int, since: date | None = None) -> None:
        row = {
            "customer_sk": self.next_sk, "customer_id": rec["customer_id"], "customer_name": rec["customer_name"],
            "country_code": rec["country_code"], "segment": rec["segment"], "is_key_account": rec["is_key_account"],
            "sales_rep": rec["sales_rep"], "customer_since": since or rec["created_at"],
            "valid_from": valid_from, "valid_to": OPEN_END, "is_current": True, "is_inferred": False, "version": version,
        }
        self.next_sk += 1
        self.rows.append(row)
        self.current[rec["customer_id"]] = len(self.rows) - 1

    def sk_for(self, customer_id: str, country_code: str, order_date: date) -> int:
        idx = self.current.get(customer_id)
        if idx is None:
            self._insert({"customer_id": customer_id, "customer_name": f"Unknown customer {customer_id}",
                          "country_code": country_code, "segment": "Unknown", "is_key_account": False,
                          "sales_rep": "Unassigned", "created_at": order_date}, valid_from=BEGINNING, version=1)
            self.rows[-1]["is_inferred"] = True
            self.stats["inferred"] += 1
            idx = self.current[customer_id]
        return self.rows[idx]["customer_sk"]

    def to_frame(self) -> pl.DataFrame:
        schema = {"customer_sk": pl.Int32, "customer_id": pl.String, "customer_name": pl.String, "country_code": pl.String,
                  "segment": pl.String, "is_key_account": pl.Boolean, "sales_rep": pl.String, "customer_since": pl.Date,
                  "valid_from": pl.Date, "valid_to": pl.Date, "is_current": pl.Boolean, "is_inferred": pl.Boolean,
                  "version": pl.Int32}
        return pl.DataFrame(self.rows, schema=schema)
