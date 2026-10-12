"""End-to-end and unit tests. Run: pytest -q"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from exportlake.config import Paths
from exportlake.generator import generate
from exportlake.pipeline.model import CustomerDimension
from exportlake.pipeline.run import run
from exportlake.quality import apply_contract

START, MID, END = date(2025, 1, 1), date(2025, 3, 14), date(2025, 3, 31)


@pytest.fixture(scope="module")
def lake(tmp_path_factory) -> Path:
    """Two incremental runs on a short window, like two scheduled days."""
    root = tmp_path_factory.mktemp("lake")
    p = Paths(root).ensure()
    generate(START, MID, p.landing)
    _restrict_landing(p, START)
    run(root, until=MID, allow_network=False)
    generate(START, END, p.landing)
    _restrict_landing(p, START)
    run(root, until=END, allow_network=False)
    return root


def _restrict_landing(p: Paths, start: date) -> None:
    for f in list((p.landing / "erp").glob("erp_orders_*.csv")):
        if f.stem[-8:] < start.strftime("%Y%m%d"):
            f.unlink()


def gold(root: Path, name: str) -> pl.DataFrame:
    return pl.read_parquet(root / "data" / "gold" / f"{name}.parquet")


# --------------------------------------------------------------------------- end to end

def test_all_gold_tables_written(lake):
    for t in ("dim_date", "dim_country", "dim_channel", "dim_product", "dim_customer", "fact_sales",
              "fact_target", "fact_fx_rate", "fact_data_quality", "rls_user_country"):
        assert (lake / "data" / "gold" / f"{t}.parquet").exists(), t


def test_fact_primary_key_unique(lake):
    f = gold(lake, "fact_sales")
    assert f.height > 1000
    assert f.select(["order_id", "line_no"]).is_duplicated().sum() == 0


def test_referential_integrity(lake):
    f = gold(lake, "fact_sales")
    for col, dim, key in [("customer_sk", "dim_customer", "customer_sk"), ("product_sk", "dim_product", "product_sk"),
                          ("date_key", "dim_date", "date_key"), ("country_code", "dim_country", "country_code"),
                          ("channel_key", "dim_channel", "channel_key")]:
        missing = f.join(gold(lake, dim).select(key), left_on=col, right_on=key, how="anti").height
        assert missing == 0, f"{col} has {missing} orphan rows"


def test_every_line_has_fx_and_eur_amounts(lake):
    f = gold(lake, "fact_sales")
    assert f["fx_rate"].null_count() == 0
    assert f["net_eur"].null_count() == 0
    eur = f.filter(pl.col("currency") == "EUR")
    assert (eur["fx_rate"] == 1.0).all()


def test_returns_are_negative(lake):
    f = gold(lake, "fact_sales")
    assert (f.filter(pl.col("is_return"))["quantity"] < 0).all()
    assert (f.filter(~pl.col("is_return"))["quantity"] > 0).all()


def test_incremental_run_is_idempotent(lake):
    before = gold(lake, "fact_sales").height
    run(lake, until=END, allow_network=False)  # nothing new to load
    assert gold(lake, "fact_sales").height == before


def test_redelivered_file_replaces_rows(lake):
    """If the ERP re-sends a day, its rows are replaced, not duplicated."""
    import json
    p = Paths(lake)
    state = json.loads((p.state / "watermark.json").read_text())
    state["last_business_date"] = "2025-03-27"
    (p.state / "watermark.json").write_text(json.dumps(state))
    before = gold(lake, "fact_sales").height
    run(lake, until=END, allow_network=False)
    assert gold(lake, "fact_sales").height == before


def test_quarantine_and_dq_log(lake):
    dq = gold(lake, "fact_data_quality")
    assert dq["run_id"].n_unique() >= 2
    q = pl.read_parquet(lake / "data" / "quarantine" / "quarantine.parquet")
    assert q.height > 0 and q["reasons"].str.len_chars().min() > 0


def test_scd2_has_single_current_version(lake):
    c = gold(lake, "dim_customer")
    assert c.filter(pl.col("is_current")).group_by("customer_id").len()["len"].max() == 1
    assert c["customer_sk"].is_unique().all()
    closed = c.filter(~pl.col("is_current"))
    assert (closed["valid_to"] < date(9999, 12, 31)).all()


def test_targets_cover_all_countries(lake):
    t = gold(lake, "fact_target").filter(pl.col("month_start").dt.year() == 2025)
    assert t["country_code"].n_unique() >= 7
    assert (t["target_eur"] > 0).all()


# --------------------------------------------------------------------------- units

def test_contract_cleanses_casts_and_quarantines():
    raw = pl.DataFrame({
        "Auftragsnr": ["A0000001", "A0000001", "A0000002", "A0000003"],
        "Pos": ["1", "1", "1", "1"],
        "Auftragsdatum": ["02.01.2025"] * 4,
        "Kundennr": ["C10001", "C10001", "", "C10002"],
        "Artikelnr": [" vc-hy-1001 ", "VC-HY-1001", "VC-HY-1001", "VC-HY-1001"],
        "Menge": ["2", "2", "1", "0"],
        "Einzelpreis": ["1.234,50"] * 4,
        "Rabatt %": ["5,0"] * 4,
        "Währung": ["eur", "EUR", "EUR", "EUR"],
        "Lieferland": ["DE"] * 4,
        "Vertriebskanal": ["Direct"] * 4,
        "Status": ["Geliefert"] * 4,
    })
    valid, bad, res = apply_contract(raw, "erp_order_lines", "test.csv", {"product.sku": {"VC-HY-1001"}})
    assert valid.height == 1                                  # duplicate removed, 2 quarantined
    assert valid["unit_price_local"][0] == pytest.approx(1234.5)
    assert valid["sku"][0] == "VC-HY-1001" and valid["currency"][0] == "EUR"
    assert bad.height == 2
    assert {"customer_id: required", "quantity: <> 0"} <= set("; ".join(bad["_reasons"]).split("; "))
    assert any(r.action == "auto-fixed" for r in res)


def test_scd2_change_and_inferred_member():
    dim = CustomerDimension()
    base = {"customer_id": "C10001", "customer_name": "A GmbH", "country_code": "DE", "segment": "SME Workshop",
            "is_key_account": False, "sales_rep": "Lena Hartmann", "created_at": date(2024, 1, 1), "updated_at": date(2024, 1, 1)}
    dim.apply_change(base)
    sk1 = dim.sk_for("C10001", "DE", date(2024, 2, 1))
    dim.apply_change({**base, "segment": "Industrial OEM", "updated_at": date(2024, 6, 1)})
    sk2 = dim.sk_for("C10001", "DE", date(2024, 7, 1))
    assert sk1 != sk2 and dim.stats["changed"] == 1
    df = dim.to_frame()
    assert df.filter(pl.col("customer_sk") == sk1)["valid_to"][0] == date(2024, 5, 31)
    # order before CRM record -> inferred member, later completed in place (same key)
    sk3 = dim.sk_for("C20000", "FR", date(2024, 3, 1))
    dim.apply_change({**base, "customer_id": "C20000", "country_code": "FR", "customer_name": "B SAS"})
    row = dim.to_frame().filter(pl.col("customer_sk") == sk3)
    assert row["is_inferred"][0] is False and row["customer_name"][0] == "B SAS"
    assert dim.to_frame().filter(pl.col("customer_id") == "C20000").height == 1
