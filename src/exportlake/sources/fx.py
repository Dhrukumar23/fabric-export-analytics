"""ECB euro foreign exchange reference rates.

Live source: the ECB Data Portal SDMX API (no key needed). Inside GitHub Actions
or a Fabric notebook this returns the real published rates. When the API cannot
be reached (offline laptop, sandbox) the pipeline falls back to the synthetic
file the generator wrote, and records that choice in the data-quality log.
"""
from __future__ import annotations

import io
import urllib.request
from datetime import date
from pathlib import Path

import polars as pl

from ..config import FOREIGN_CURRENCIES

ECB_URL = ("https://data-api.ecb.europa.eu/service/data/EXR/D.{curs}.EUR.SP00.A"
           "?format=csvdata&startPeriod={start}&endPeriod={end}")


def fetch_ecb(start: date, end: date, landing: Path, timeout: int = 30) -> Path:
    url = ECB_URL.format(curs="+".join(FOREIGN_CURRENCIES), start=start.isoformat(), end=end.isoformat())
    req = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": "exportlake/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        body = resp.read()
    out = landing / "fx" / "ecb_eurofxref.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return out


def parse_ecb_csv(path: Path) -> pl.DataFrame:
    df = pl.read_csv(io.BytesIO(path.read_bytes()), infer_schema_length=0)
    return (df.select(
                pl.col("CURRENCY").alias("currency"),
                pl.col("TIME_PERIOD").str.to_date("%Y-%m-%d").alias("rate_date"),
                pl.col("OBS_VALUE").cast(pl.Float64, strict=False).alias("rate_per_eur"))
              .drop_nulls()
              .sort(["currency", "rate_date"]))


def load_rates(start: date, end: date, landing: Path, allow_network: bool = True) -> tuple[pl.DataFrame, str]:
    """Return (rates, source_label). Rates are units of currency per 1 EUR, one row per published day."""
    if allow_network:
        try:
            df = parse_ecb_csv(fetch_ecb(start, end, landing))
            if df.height > 0:
                return df, "ECB Data Portal (live)"
        except Exception:  # noqa: BLE001 - any network/parse failure falls back
            pass
    cached = landing / "fx" / "ecb_eurofxref.csv"
    if cached.exists():
        df = parse_ecb_csv(cached)
        if df.height > 0 and df["rate_date"].max() >= end:
            return df, "ECB Data Portal (cached)"
    return parse_ecb_csv(landing / "fx" / "ecb_eurofxref_fallback.csv"), "Synthetic fallback (ECB offline)"
