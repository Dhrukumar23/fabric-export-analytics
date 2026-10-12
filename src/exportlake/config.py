"""Static business configuration for the fictional company Valdora Components GmbH.

Valdora is a fictional German B2B manufacturer of industrial components that
exports to 9 countries in 7 currencies. All company, customer and product
names in this project are invented.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

COMPANY = "Valdora Components GmbH (fictional)"
HISTORY_START = date(2024, 1, 1)


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    region: str
    currency: str
    weight: float          # share of order volume once the market is live
    market_entry: date     # first day orders can occur
    growth: float          # yearly volume growth


COUNTRIES: tuple[Country, ...] = (
    Country("DE", "Germany", "DACH", "EUR", 0.30, date(2024, 1, 1), 0.02),
    Country("CH", "Switzerland", "DACH", "CHF", 0.07, date(2024, 1, 1), 0.03),
    Country("FR", "France", "Western Europe", "EUR", 0.12, date(2024, 1, 1), 0.01),
    Country("IT", "Italy", "Western Europe", "EUR", 0.10, date(2024, 1, 1), 0.00),
    Country("GB", "United Kingdom", "Western Europe", "GBP", 0.09, date(2024, 1, 1), 0.02),
    Country("PL", "Poland", "Central & Northern Europe", "PLN", 0.08, date(2024, 1, 1), 0.09),
    Country("SE", "Sweden", "Central & Northern Europe", "SEK", 0.06, date(2024, 1, 1), 0.03),
    Country("US", "United States", "North America", "USD", 0.12, date(2025, 3, 1), 0.45),
    Country("JP", "Japan", "Asia-Pacific", "JPY", 0.06, date(2025, 9, 1), 0.35),
)
COUNTRY_BY_CODE = {c.code: c for c in COUNTRIES}
CURRENCIES = sorted({c.currency for c in COUNTRIES})
FOREIGN_CURRENCIES = [c for c in CURRENCIES if c != "EUR"]

CHANNELS = ("Direct", "Distributor", "E-Procurement")
SEGMENTS = ("Key Account", "Industrial OEM", "Distributor", "SME Workshop")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def landing(self) -> Path:          # raw files exactly as delivered by source systems
        return self.root / "data" / "landing"

    @property
    def gold(self) -> Path:             # star schema served to Power BI (single parquet per table)
        return self.root / "data" / "gold"

    @property
    def lake_tables(self) -> Path:      # Delta tables, same layout as a Fabric Lakehouse /Tables folder
        return self.root / "data" / "lakehouse" / "Tables"

    @property
    def quarantine(self) -> Path:
        return self.root / "data" / "quarantine"

    @property
    def state(self) -> Path:
        return self.root / "data" / "state"

    def ensure(self) -> "Paths":
        for p in (self.landing, self.gold, self.lake_tables, self.quarantine, self.state):
            p.mkdir(parents=True, exist_ok=True)
        return self
