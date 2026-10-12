"""Deterministic simulation of Valdora's business world.

Everything here is reproducible: the same date always produces the same
orders, so the daily GitHub Action can regenerate any source file and the
pipeline can be re-run idempotently.
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import lru_cache

from ..config import COUNTRIES, COUNTRY_BY_CODE, FOREIGN_CURRENCIES, HISTORY_START

SEED = 20240101
HORIZON = date(2028, 12, 31)

# --------------------------------------------------------------------------- products

CATALOG = {
    # category: (code, cost ratio, {subcategory: (n products, min price, max price)})
    "Hydraulics": ("HY", 0.58, {"Pumps": (5, 900, 4200), "Valves": (6, 120, 900), "Cylinders": (4, 450, 2600)}),
    "Pneumatics": ("PN", 0.52, {"Actuators": (5, 180, 1300), "Fittings": (6, 8, 60)}),
    "Sensors": ("SE", 0.45, {"Pressure Sensors": (4, 140, 780), "Proximity Sensors": (5, 45, 260), "Flow Sensors": (3, 380, 1900)}),
    "Drive Technology": ("DR", 0.63, {"Gear Motors": (5, 700, 5200), "Couplings": (4, 60, 420)}),
    "Spare Parts": ("SP", 0.34, {"Seals & Kits": (6, 15, 140), "Filters": (5, 25, 220)}),
}
NAME_PARTS = {
    "Pumps": ["Axial Piston Pump", "Gear Pump", "Vane Pump", "Radial Piston Pump", "Load-Sensing Pump"],
    "Valves": ["Directional Valve", "Pressure Relief Valve", "Proportional Valve", "Check Valve", "Flow Control Valve", "Cartridge Valve"],
    "Cylinders": ["Tie-Rod Cylinder", "Welded Cylinder", "Compact Cylinder", "Telescopic Cylinder"],
    "Actuators": ["Rotary Actuator", "Guided Actuator", "ISO Cylinder", "Gripper Actuator", "Rodless Actuator"],
    "Fittings": ["Push-In Fitting", "Elbow Fitting", "Tee Fitting", "Quick Coupler", "Bulkhead Fitting", "Swivel Fitting"],
    "Pressure Sensors": ["Pressure Transmitter", "Pressure Switch", "Differential Sensor", "Hydraulic Pressure Sensor"],
    "Proximity Sensors": ["Inductive Sensor", "Capacitive Sensor", "Magnetic Sensor", "Ultrasonic Sensor", "Photoelectric Sensor"],
    "Flow Sensors": ["Turbine Flow Meter", "Vortex Flow Sensor", "Thermal Flow Switch"],
    "Gear Motors": ["Helical Gear Motor", "Bevel Gear Motor", "Planetary Gear Motor", "Worm Gear Motor", "Servo Gear Motor"],
    "Couplings": ["Jaw Coupling", "Disc Coupling", "Gear Coupling", "Safety Coupling"],
    "Seals & Kits": ["O-Ring Kit", "Rod Seal Kit", "Piston Seal Set", "Wiper Seal", "Valve Repair Kit", "Pump Seal Kit"],
    "Filters": ["Return Line Filter", "Pressure Filter", "Suction Strainer", "Breather Filter", "Filter Element"],
}
SIZES = ["S", "M", "L", "XL", "HD", "Pro"]


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    subcategory: str
    list_price_eur: float
    unit_cost_eur: float


@lru_cache(maxsize=1)
def products() -> tuple[Product, ...]:
    rng = random.Random(SEED + 1)
    out: list[Product] = []
    for category, (code, cost_ratio, subs) in CATALOG.items():
        n = 1001
        for sub, (count, lo, hi) in subs.items():
            names = NAME_PARTS[sub]
            for i in range(count):
                price = round(math.exp(rng.uniform(math.log(lo), math.log(hi))), 2)
                cost = round(price * cost_ratio * rng.uniform(0.9, 1.1), 2)
                name = f"{names[i % len(names)]} {SIZES[rng.randrange(len(SIZES))]}-{rng.randint(10, 99)}"
                out.append(Product(f"VC-{code}-{n}", name, category, sub, price, cost))
                n += 1
    return tuple(out)


# --------------------------------------------------------------------------- customers

SYL = ["Ber", "Kon", "Ma", "Tri", "Vel", "Sto", "Lum", "Hal", "Ner", "Ost", "Pra", "Qui", "Ram", "Sil",
       "Tor", "Ul", "Var", "Wen", "Zel", "Am", "Bru", "Cor", "Del", "Fen", "Gra", "Ive", "Jor", "Lin"]
END = ["tec", "dra", "max", "ion", "flex", "tron", "werk", "line", "nova", "mark", "form", "dyn"]
INDUSTRY = ["Maschinenbau", "Automation", "Industrietechnik", "Hydraulik", "Fluid Systems", "Engineering",
            "Agrartechnik", "Fördertechnik", "Precision", "Industrial", "Mechatronics", "Robotics"]
LEGAL = {"DE": ["GmbH", "GmbH & Co. KG", "AG"], "CH": ["AG", "GmbH", "SA"], "FR": ["SAS", "SA", "SARL"],
         "IT": ["S.r.l.", "S.p.A."], "GB": ["Ltd", "plc"], "PL": ["Sp. z o.o.", "S.A."], "SE": ["AB"],
         "US": ["Inc.", "LLC", "Corp."], "JP": ["K.K.", "Co., Ltd."]}
REPS = {"DACH": ["Lena Hartmann", "Tobias Becker", "Sabine Wolf"],
        "Western Europe": ["Claire Dubois", "Marco Rinaldi", "Oliver Grant"],
        "Central & Northern Europe": ["Agnieszka Nowak", "Erik Lindqvist"],
        "North America": ["Megan Brooks", "Daniel Ortiz"],
        "Asia-Pacific": ["Haruto Sato"]}
SEGMENT_MIX = [("Key Account", 0.08), ("Industrial OEM", 0.34), ("Distributor", 0.22), ("SME Workshop", 0.36)]
SEGMENT_ORDER_WEIGHT = {"Key Account": 7.0, "Industrial OEM": 2.2, "Distributor": 3.0, "SME Workshop": 1.0}


@dataclass(frozen=True)
class CustomerVersion:
    customer_id: str
    name: str
    country: str
    segment: str
    sales_rep: str
    created: date        # first day the customer can order (ERP knows the customer number)
    crm_published: date  # day the CRM record reaches the export (can lag behind first orders)
    updated: date        # date of this attribute version


@dataclass
class CustomerTimeline:
    versions: dict[str, list[CustomerVersion]] = field(default_factory=dict)

    def as_of(self, d: date) -> list[CustomerVersion]:
        out = []
        for vs in self.versions.values():
            current = None
            for v in vs:
                if v.updated <= d:
                    current = v
            if current is not None and current.created <= d:
                out.append(current)
        return out


def _pick(rng: random.Random, weighted):
    r, acc = rng.random() * sum(w for _, w in weighted), 0.0
    for item, w in weighted:
        acc += w
        if r <= acc:
            return item
    return weighted[-1][0]


def _company_name(rng: random.Random, country: str) -> str:
    stem = rng.choice(SYL) + rng.choice(END)
    return f"{stem} {rng.choice(INDUSTRY)} {rng.choice(LEGAL[country])}"


@lru_cache(maxsize=1)
def customer_timeline() -> CustomerTimeline:
    rng = random.Random(SEED + 2)
    tl = CustomerTimeline()
    seq = 10001

    def new_customer(country, created: date, lag_days: int):
        nonlocal seq
        c = COUNTRY_BY_CODE[country]
        cid = f"C{seq}"
        seq += 1
        v = CustomerVersion(cid, _company_name(rng, country), country, _pick(rng, SEGMENT_MIX),
                            rng.choice(REPS[c.region]), created, created + timedelta(days=lag_days), created)
        tl.versions[cid] = [v]

    for c in COUNTRIES:  # initial customer base at market entry
        n0 = int(round(c.weight * (420 if c.market_entry == HISTORY_START else 160)))
        for _ in range(n0):
            new_customer(c.code, c.market_entry, 0)

    d = HISTORY_START + timedelta(days=1)
    while d <= HORIZON:
        for c in COUNTRIES:
            if d < c.market_entry:
                continue
            rate = c.weight * 0.30 * (2.5 if c.growth > 0.2 else 1.0)
            if rng.random() < rate:
                lag = rng.choice([0, 0, 0, 1, 2, 3])
                new_customer(c.code, d, lag)
        if rng.random() < 0.07:  # attribute change on an existing customer -> SCD Type 2 history
            cid = rng.choice(list(tl.versions))
            cur = tl.versions[cid][-1]
            if cur.created < d and cur.crm_published < d:
                kind = rng.random()
                if kind < 0.5:
                    reps = [r for r in REPS[COUNTRY_BY_CODE[cur.country].region] if r != cur.sales_rep] or [cur.sales_rep]
                    nv = replace(cur, sales_rep=rng.choice(reps), updated=d)
                elif kind < 0.85:
                    up = {"SME Workshop": "Industrial OEM", "Industrial OEM": "Key Account"}.get(cur.segment, cur.segment)
                    nv = replace(cur, segment=up, updated=d)
                else:
                    nv = replace(cur, segment="Industrial OEM" if cur.segment == "Key Account" else cur.segment, updated=d)
                if nv != cur:
                    tl.versions[cid].append(nv)
        d += timedelta(days=1)
    return tl


# --------------------------------------------------------------------------- FX (synthetic fallback)

FX_START = {"USD": 1.09, "GBP": 0.86, "CHF": 0.94, "PLN": 4.33, "SEK": 11.4, "JPY": 158.0}
FX_DRIFT = {"USD": 0.00004, "GBP": 0.00001, "CHF": -0.00002, "PLN": -0.00001, "SEK": 0.0, "JPY": 0.00010}


@lru_cache(maxsize=1)
def synthetic_fx() -> dict[str, dict[date, float]]:
    """Units of foreign currency per 1 EUR (ECB convention), every calendar day."""
    rng = random.Random(SEED + 3)
    out: dict[str, dict[date, float]] = {cur: {} for cur in FOREIGN_CURRENCIES}
    level = {cur: math.log(FX_START[cur]) for cur in FOREIGN_CURRENCIES}
    d = date(2023, 1, 1)
    while d <= HORIZON:
        for cur in FOREIGN_CURRENCIES:
            level[cur] += FX_DRIFT[cur] + rng.gauss(0, 0.0035)
            out[cur][d] = round(math.exp(level[cur]), 4)
        d += timedelta(days=1)
    return out


def fx_rate(currency: str, d: date) -> float:
    return 1.0 if currency == "EUR" else synthetic_fx()[currency][d]


# --------------------------------------------------------------------------- orders

SEASON = {1: 0.90, 2: 0.95, 3: 1.05, 4: 1.00, 5: 1.00, 6: 1.02, 7: 0.94, 8: 0.74, 9: 1.06, 10: 1.08, 11: 1.05, 12: 0.80}
DISCOUNT = {"Key Account": (8, 14), "Distributor": (15, 24), "Industrial OEM": (3, 8), "SME Workshop": (0, 4)}
CATEGORY_AFFINITY = {"Distributor": {"Spare Parts": 2.0, "Pneumatics": 1.5}, "Key Account": {"Hydraulics": 1.8, "Drive Technology": 1.6},
                     "Industrial OEM": {"Sensors": 1.6, "Drive Technology": 1.3}, "SME Workshop": {"Spare Parts": 2.2}}
BASE_ORDERS_PER_WEEKDAY = 30.0


@dataclass(frozen=True)
class OrderLine:
    order_id: str
    line_no: int
    order_date: date
    customer_id: str
    sku: str
    quantity: int
    unit_price_local: float
    discount_pct: float
    currency: str
    channel: str
    status: str


def _volume_factor(c, d: date) -> float:
    if d < c.market_entry:
        return 0.0
    years = (d - max(c.market_entry, HISTORY_START)).days / 365.25
    ramp = min(1.0, ((d - c.market_entry).days + 30) / 270) if c.market_entry > HISTORY_START else 1.0
    return c.weight * (1 + c.growth) ** years * ramp


def _price_local(p: Product, currency: str, d: date) -> float:
    v = p.list_price_eur * fx_rate(currency, d)
    return float(round(v)) if currency == "JPY" else round(v, 2)


@lru_cache(maxsize=4096)
def orders_for_day(d: date, seed_offset: int = 0) -> tuple[OrderLine, ...]:
    """Original order lines booked on day d (no returns, no data-quality noise)."""
    if d.weekday() == 6:
        return ()
    rng = random.Random(SEED * 31 + d.toordinal() * 7 + seed_offset)
    weekday_factor = 0.18 if d.weekday() == 5 else 1.0
    prods = products()
    active = [v for v in customer_timeline().as_of(d)]
    by_country: dict[str, list[CustomerVersion]] = {}
    for v in active:
        by_country.setdefault(v.country, []).append(v)
    lines: list[OrderLine] = []
    seq = 0
    for c in COUNTRIES:
        lam = BASE_ORDERS_PER_WEEKDAY * _volume_factor(c, d) * SEASON[d.month] * weekday_factor
        if lam <= 0 or c.code not in by_country:
            continue
        n_orders = _poisson(rng, lam)
        pool = by_country[c.code]
        weights = [(v, SEGMENT_ORDER_WEIGHT[v.segment]) for v in pool]
        for _ in range(n_orders):
            cust = _pick(rng, weights)
            seq += 1
            order_id = f"A{(d - HISTORY_START).days + 1:04d}{seq:03d}"
            channel = "Distributor" if cust.segment == "Distributor" else ("E-Procurement" if rng.random() < 0.28 else "Direct")
            aff = CATEGORY_AFFINITY.get(cust.segment, {})
            pw = [(p, aff.get(p.category, 1.0)) for p in prods]
            for ln in range(1, 1 + _pick(rng, [(1, 0.35), (2, 0.3), (3, 0.2), (4, 0.1), (5, 0.05)])):
                p = _pick(rng, pw)
                scale = (180 / p.list_price_eur) ** 0.6
                mult = 3.0 if cust.segment == "Key Account" else (2.0 if cust.segment == "Distributor" else 1.0)
                qty = max(1, int(rng.gammavariate(2.0, 2.2 * scale * mult)))
                lo, hi = DISCOUNT[cust.segment]
                disc = round(rng.uniform(lo, hi) * 2) / 2
                lines.append(OrderLine(order_id, ln, d, cust.customer_id, p.sku, qty,
                                       _price_local(p, c.currency, d), disc, c.currency, channel, "Geliefert"))
    return tuple(lines)


def returns_for_day(d: date) -> list[OrderLine]:
    """Return lines (negative quantity) booked on day d, referencing orders 5-30 days earlier."""
    out = []
    for back in range(5, 31):
        od = d - timedelta(days=back)
        if od < HISTORY_START:
            continue
        for ol in orders_for_day(od):
            r = random.Random(zlib.crc32(f"{ol.order_id}-{ol.line_no}-{SEED}".encode()))
            if r.random() < 0.022 and r.randint(5, 30) == back:
                q = -max(1, int(ol.quantity * r.choice([1.0, 1.0, 0.5])))
                out.append(replace(ol, line_no=900 + ol.line_no, order_date=d, quantity=q, status="Retoure"))
    return out


def _poisson(rng: random.Random, lam: float) -> int:
    if lam > 60:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1
