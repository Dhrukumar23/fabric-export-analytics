"""Writes source-system extracts to the landing zone, exactly as the systems would deliver them.

The files are deliberately imperfect, the way real exports are:
  ERP  - ';' delimited, Windows-1252, German decimals and dates, re-exported duplicates,
         untrimmed / lower-case article numbers, missing customer numbers, unknown SKUs, zero quantities
  CRM  - JSON change feed from a REST API; new customers can arrive days after their first order
  Controlling - yearly budget in wide format (one column per month, EUR thousands)
  FX   - ECB reference rates (fetched live by the pipeline; a synthetic file is the offline fallback)
"""
from __future__ import annotations

import csv
import io
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from ..config import COUNTRIES, FOREIGN_CURRENCIES, HISTORY_START
from . import world

MONTHS_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
ERP_HEADER = ["Auftragsnr", "Pos", "Auftragsdatum", "Kundennr", "Artikelnr", "Menge", "Einzelpreis",
              "Rabatt %", "Währung", "Lieferland", "Vertriebskanal", "Status"]
STATUS_NOISE_SEED = 77


def de_number(x: float, decimals: int = 2) -> str:
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------------------- ERP


def erp_lines_for_day(d: date) -> list[list[str]]:
    rng = random.Random(world.SEED * 13 + d.toordinal() + STATUS_NOISE_SEED)
    rows: list[list[str]] = []
    for ol in list(world.orders_for_day(d)) + world.returns_for_day(d):
        sku = ol.sku
        cust = ol.customer_id
        qty = ol.quantity
        cur = ol.currency
        r = rng.random()
        if r < 0.005:
            sku = f" {sku.lower()} "                 # fixable: trim + upper
        elif r < 0.007:
            cust = ""                                # not fixable -> quarantine
        elif r < 0.008:
            sku = sku[:-4] + "9" + sku[-3:]           # unknown article -> quarantine
        elif r < 0.0095:
            qty = 0                                  # invalid quantity -> quarantine
        elif r < 0.015:
            cur = cur.lower()                        # fixable
        dec = 0 if ol.currency == "JPY" else 2
        row = [ol.order_id, str(ol.line_no), ol.order_date.strftime("%d.%m.%Y"), cust, sku, str(qty),
               de_number(ol.unit_price_local, dec), de_number(ol.discount_pct, 1), cur, customer_country()[ol.customer_id],
               ol.channel, ol.status]
        rows.append(row)
        if rng.random() < 0.008:
            rows.append(list(row))                   # re-exported duplicate
    return rows


def write_erp_export(d: date, landing: Path) -> Path | None:
    rows = erp_lines_for_day(d)
    if not rows:
        return None
    out = landing / "erp" / f"erp_orders_{d:%Y%m%d}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(ERP_HEADER)
    w.writerows(rows)
    out.write_bytes(buf.getvalue().encode("cp1252"))
    return out


# --------------------------------------------------------------------------- CRM


def crm_changes_for_day(d: date) -> list[dict]:
    items = []
    for vs in world.customer_timeline().versions.values():
        for i, v in enumerate(vs):
            publish = v.crm_published if i == 0 else v.updated
            if publish == d or (d == HISTORY_START and publish < d):
                items.append({
                    "id": v.customer_id,
                    "company": {"name": v.name, "legalCountry": v.country},
                    "classification": {"segment": v.segment, "keyAccount": v.segment == "Key Account"},
                    "owner": {"fullName": v.sales_rep},
                    "createdAt": f"{v.created.isoformat()}T08:00:00Z",
                    "updatedAt": f"{v.updated.isoformat()}T{9 + i % 8:02d}:15:00Z",
                })
    return items


def write_crm_changes(d: date, landing: Path) -> Path | None:
    items = crm_changes_for_day(d)
    if not items:
        return None
    out = landing / "crm" / f"crm_customers_changes_{d:%Y%m%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {"endpoint": "/v2/customers/changes", "since": f"{d.isoformat()}T00:00:00Z",
                        "count": len(items)}, "data": items}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- master data, budget, FX


def write_product_master(landing: Path) -> Path:
    out = landing / "erp" / "product_master.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Artikelnr", "Bezeichnung", "Warengruppe", "Untergruppe", "Listenpreis EUR", "Standardkosten EUR"])
    for p in world.products():
        w.writerow([p.sku, p.name, p.category, p.subcategory, de_number(p.list_price_eur), de_number(p.unit_cost_eur)])
    out.write_bytes(buf.getvalue().encode("cp1252"))
    return out


def write_budget(year: int, landing: Path) -> Path:
    """Controlling plans revenue per country and month in EUR thousands (wide format)."""
    plan = defaultdict(float)
    d = date(year, 1, 1)
    prices = {p.sku: p for p in world.products()}
    while d.year == year:
        for ol in world.orders_for_day(d, seed_offset=99991):  # a plan scenario, not the actuals
            net_eur = prices[ol.sku].list_price_eur * ol.quantity * (1 - ol.discount_pct / 100)
            plan[(customer_country()[ol.customer_id], d.month)] += net_eur
        d += timedelta(days=1)
    uplift = {"US": 0.88, "JP": 0.92}
    out = landing / "controlling" / f"budget_umsatz_{year}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["Land;" + ";".join(MONTHS_DE)]
    rng = random.Random(year)
    for c in COUNTRIES:
        annual = sum(plan.get((c.code, m), 0.0) for m in range(1, 13)) * uplift.get(c.code, 1.03)
        # phase the annual plan by seasonality x working days x market ramp-up, like controlling does
        w = []
        for m in range(1, 13):
            mid = date(year, m, 15)
            workdays = sum(1 for i in range(31) if (date(year, m, 1) + timedelta(days=i)).month == m
                           and (date(year, m, 1) + timedelta(days=i)).weekday() < 5)
            w.append(world.SEASON[m] * workdays * world._volume_factor(c, mid))
        tot = sum(w) or 1.0
        vals = [de_number(annual * wi / tot * rng.uniform(0.99, 1.01) / 1000, 1) for wi in w]
        lines.append(f"{c.name};" + ";".join(vals))
    out.write_text("\r\n".join(lines) + "\r\n", encoding="cp1252")
    return out


_cc_cache: dict[str, str] = {}


def customer_country() -> dict[str, str]:
    if not _cc_cache:
        for cid, vs in world.customer_timeline().versions.items():
            _cc_cache[cid] = vs[0].country
    return _cc_cache


def write_fx_fallback(start: date, end: date, landing: Path) -> Path:
    """Synthetic file in ECB 'csvdata' layout, used only when the ECB API is unreachable."""
    out = landing / "fx" / "ecb_eurofxref_fallback.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = ["KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE"]
    fx = world.synthetic_fx()
    d = start
    while d <= end:
        if d.weekday() < 5:  # ECB publishes on TARGET business days only
            for cur in FOREIGN_CURRENCIES:
                rows.append(f"EXR.D.{cur}.EUR.SP00.A,D,{cur},EUR,SP00,A,{d.isoformat()},{fx[cur][d]}")
        d += timedelta(days=1)
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return out


# --------------------------------------------------------------------------- orchestration


def generate(start: date, end: date, landing: Path) -> dict[str, int]:
    """Create every source extract for business days start..end (inclusive)."""
    counts = defaultdict(int)
    write_product_master(landing)
    years = list(range(HISTORY_START.year, end.year + 1 + (1 if end.month == 12 else 0)))
    for y in years:
        if not (landing / "controlling" / f"budget_umsatz_{y}.csv").exists():
            write_budget(y, landing)
            counts["budget"] += 1
    write_fx_fallback(date(HISTORY_START.year - 1, 10, 1), end, landing)
    d = start
    while d <= end:
        if write_erp_export(d, landing):
            counts["erp"] += 1
        if write_crm_changes(d, landing):
            counts["crm"] += 1
        d += timedelta(days=1)
    return dict(counts)
