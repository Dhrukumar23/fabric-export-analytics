"""Generates the Power BI Project (PBIP) from code: TMDL semantic model + PBIR report.

Why generate it? The model definition lives next to the pipeline in Git, every
measure is reviewed like code, and CI can lint it. Open
powerbi/ValdoraSales.pbip in Power BI Desktop to use it.

    python tools/build_pbip.py --github-user Dhrukumar23
"""
from __future__ import annotations

import argparse
import json
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "powerbi"
NAME = "ValdoraSales"
NS = uuid.UUID("6f1c2a52-3d0e-4c1a-9a8e-0b5d1c7e2f10")
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def q(name: str) -> str:
    """Quote a TMDL/DAX object name when needed."""
    return name if name.replace("_", "").isalnum() else "'" + name.replace("'", "''") + "'"


def indent(text: str, n: int) -> str:
    pad = "\t" * n
    return "\n".join(pad + line if line.strip() else "" for line in dedent(text).strip("\n").splitlines())


# =========================================================================== semantic model

@dataclass
class Col:
    name: str
    source: str
    dtype: str                 # string | int64 | double | dateTime | boolean
    fmt: str | None = None
    hidden: bool = False
    sort_by: str | None = None
    key: bool = False
    summarize: str = "none"
    category: str | None = None
    desc: str | None = None
    folder: str | None = None


@dataclass
class Measure:
    name: str
    expr: str
    fmt: str | None = None
    folder: str | None = None
    desc: str | None = None
    fmt_expr: str | None = None


@dataclass
class Table:
    name: str
    gold: str | None
    cols: list[Col]
    measures: list[Measure] = field(default_factory=list)
    hidden: bool = False
    desc: str | None = None
    data_category: str | None = None


EUR = '"€"#,0'
EUR_K = '"€"#,0,"k"'
PCT = "0.0%"
INT = "#,0"

CAL = Table("Calendar", "dim_date", [
    Col("Date", "date", "dateTime", "dd.mm.yyyy", key=True),
    Col("Date Key", "date_key", "int64", "0", hidden=True),
    Col("Year", "year", "int64", "0"),
    Col("Quarter", "quarter", "string"),
    Col("Year Quarter", "year_quarter", "string"),
    Col("Month No", "month_no", "int64", "0", hidden=True),
    Col("Month", "month_name", "string", sort_by="Month No"),
    Col("Month Short", "month_short", "string", sort_by="Month No"),
    Col("Year Month No", "year_month_no", "int64", "0", hidden=True),
    Col("Year Month", "year_month", "string", sort_by="Year Month No"),
    Col("Month Start", "month_start", "dateTime", "mmm yyyy"),
    Col("ISO Week", "iso_week", "int64", "0"),
    Col("Weekday No", "weekday_no", "int64", "0", hidden=True),
    Col("Weekday", "weekday", "string", sort_by="Weekday No"),
    Col("Day of Month", "day_of_month", "int64", "0", hidden=True),
    Col("Is Working Day", "is_working_day", "boolean"),
    Col("Has Actuals", "has_actuals", "boolean", desc="TRUE up to the last business day loaded by the pipeline."),
], desc="Conformed calendar. Marked as date table. Has Actuals separates loaded days from the future.", data_category="Time")

COUNTRY = Table("Country", "dim_country", [
    Col("Country Code", "country_code", "string"),
    Col("Country", "country", "string", sort_by="Country Sort", category="Country"),
    Col("Region", "region", "string"),
    Col("Currency", "currency", "string"),
    Col("Market Entry", "market_entry_date", "dateTime", "dd.mm.yyyy"),
    Col("Country Sort", "country_sort", "int64", "0", hidden=True),
], desc="Sales countries with region and invoicing currency.")

CHANNEL = Table("Channel", "dim_channel", [
    Col("Channel Key", "channel_key", "int64", "0", hidden=True),
    Col("Channel", "channel", "string"),
])

PRODUCT = Table("Product", "dim_product", [
    Col("Product SK", "product_sk", "int64", "0", hidden=True),
    Col("SKU", "sku", "string"),
    Col("Product", "product_name", "string"),
    Col("Category", "category", "string"),
    Col("Subcategory", "subcategory", "string"),
    Col("List Price EUR", "list_price_eur", "double", EUR),
    Col("Standard Cost EUR", "unit_cost_eur", "double", EUR),
])

CUSTOMER = Table("Customer", "dim_customer", [
    Col("Customer SK", "customer_sk", "int64", "0", hidden=True),
    Col("Customer ID", "customer_id", "string"),
    Col("Customer", "customer_name", "string"),
    Col("Customer Country", "country_code", "string"),
    Col("Segment", "segment", "string"),
    Col("Is Key Account", "is_key_account", "boolean"),
    Col("Sales Rep", "sales_rep", "string"),
    Col("Customer Since", "customer_since", "dateTime", "dd.mm.yyyy"),
    Col("Valid From", "valid_from", "dateTime", "dd.mm.yyyy", folder="SCD Type 2"),
    Col("Valid To", "valid_to", "dateTime", "dd.mm.yyyy", folder="SCD Type 2"),
    Col("Is Current", "is_current", "boolean", folder="SCD Type 2"),
    Col("Is Inferred", "is_inferred", "boolean", folder="SCD Type 2",
        desc="Placeholder created when an order arrived before the CRM record (late-arriving dimension)."),
    Col("Version", "version", "int64", "0", folder="SCD Type 2"),
], desc="SCD Type 2: segment and sales rep keep their history, so revenue is reported with the attributes valid at order time.")

SALES_COLS = [
    Col("Order ID", "order_id", "string"),
    Col("Line No", "line_no", "int64", "0", hidden=True),
    Col("Order Date", "order_date", "dateTime", "dd.mm.yyyy", hidden=True),
    Col("Date Key", "date_key", "int64", "0", hidden=True),
    Col("Customer SK", "customer_sk", "int64", "0", hidden=True),
    Col("Product SK", "product_sk", "int64", "0", hidden=True),
    Col("Country Code", "country_code", "string", hidden=True),
    Col("Channel Key", "channel_key", "int64", "0", hidden=True),
    Col("Status", "status", "string"),
    Col("Is Return", "is_return", "boolean", hidden=True),
    Col("Quantity", "quantity", "int64", INT, hidden=True),
    Col("Unit Price Local", "unit_price_local", "double", "#,0.00", hidden=True),
    Col("Discount Pct", "discount_pct", "double", "0.0", hidden=True),
    Col("Invoice Currency", "currency", "string"),
    Col("FX Rate", "fx_rate", "double", "0.0000", hidden=True),
    Col("Planning Rate", "planning_rate", "double", "0.0000", hidden=True),
    Col("Gross Local", "gross_local", "double", "#,0.00", hidden=True),
    Col("Net Local", "net_local", "double", "#,0.00", hidden=True),
    Col("Gross EUR", "gross_eur", "double", EUR, hidden=True),
    Col("Discount EUR", "discount_eur", "double", EUR, hidden=True),
    Col("Net EUR", "net_eur", "double", EUR, hidden=True),
    Col("Net EUR at Plan FX", "net_eur_plan_fx", "double", EUR, hidden=True),
    Col("Cost EUR", "cost_eur", "double", EUR, hidden=True),
    Col("Source File", "source_file", "string", hidden=True),
]

M = Measure
SALES_MEASURES = [
    # ---------------------------------------------------------------- revenue
    M("Net Revenue", "SUM ( Sales[Net EUR] )", EUR, "1 Revenue",
      "Invoiced revenue after discounts and returns, converted to EUR at the ECB rate of the order date."),
    M("Gross Revenue", "SUM ( Sales[Gross EUR] )", EUR, "1 Revenue"),
    M("Discount Amount", "SUM ( Sales[Discount EUR] )", EUR, "1 Revenue"),
    M("Discount %", "DIVIDE ( [Discount Amount], [Gross Revenue] )", PCT, "1 Revenue"),
    M("Returns", "-CALCULATE ( [Net Revenue], Sales[Is Return] = TRUE () )", EUR, "1 Revenue"),
    M("Return Rate", "DIVIDE ( [Returns], CALCULATE ( [Net Revenue], Sales[Is Return] = FALSE () ) )", PCT, "1 Revenue"),
    M("Orders", "CALCULATE ( DISTINCTCOUNT ( Sales[Order ID] ), Sales[Is Return] = FALSE () )", INT, "1 Revenue"),
    M("Avg Order Value", "DIVIDE ( CALCULATE ( [Net Revenue], Sales[Is Return] = FALSE () ), [Orders] )", EUR, "1 Revenue"),
    M("Units", "SUM ( Sales[Quantity] )", INT, "1 Revenue"),
    # ---------------------------------------------------------------- margin
    M("COGS", "SUM ( Sales[Cost EUR] )", EUR, "2 Margin", "Quantity x standard cost."),
    M("Gross Margin", "[Net Revenue] - [COGS]", EUR, "2 Margin"),
    M("Gross Margin %", "DIVIDE ( [Gross Margin], [Net Revenue] )", PCT, "2 Margin"),
    M("Gross Margin % PY", "CALCULATE ( [Gross Margin %], SAMEPERIODLASTYEAR ( 'Calendar'[Date] ) )", PCT, "2 Margin"),
    M("Gross Margin % Δ pp", "IF ( NOT ISBLANK ( [Gross Margin % PY] ), ( [Gross Margin %] - [Gross Margin % PY] ) * 100 )",
      '+0.0" pp";-0.0" pp";0.0" pp"', "2 Margin"),
    # ---------------------------------------------------------------- targets
    M("Target", """
        -- Monthly budget phased to the selected days by working days, so any date range compares fairly
        SUMX (
            VALUES ( 'Calendar'[Year Month No] ),
            VAR _monthTarget =
                CALCULATE ( SUM ( Target[Target EUR] ), ALLEXCEPT ( 'Calendar', 'Calendar'[Year Month No] ) )
            VAR _monthWorkdays =
                CALCULATE ( COUNTROWS ( 'Calendar' ), ALLEXCEPT ( 'Calendar', 'Calendar'[Year Month No] ), 'Calendar'[Is Working Day] = TRUE () )
            VAR _selectedWorkdays =
                CALCULATE ( COUNTROWS ( 'Calendar' ), 'Calendar'[Is Working Day] = TRUE () )
            RETURN
                DIVIDE ( _monthTarget * _selectedWorkdays, _monthWorkdays )
        )""", EUR, "3 Target", "Controlling budget, phased to the selected days by working days."),
    M("Target to Date", "CALCULATE ( [Target], KEEPFILTERS ( 'Calendar'[Has Actuals] = TRUE () ) )", EUR, "3 Target",
      "Target for the days that already have actuals. Use this for achievement in a running period."),
    M("Variance to Target", "[Net Revenue] - [Target to Date]", EUR, "3 Target"),
    M("Target Achievement %", "DIVIDE ( [Net Revenue], [Target to Date] )", PCT, "3 Target"),
    M("Full Year Forecast", """
        -- Actuals so far + remaining budget, scaled by the achievement rate reached so far
        VAR _year = MAX ( 'Calendar'[Year] )
        VAR _actual = CALCULATE ( [Net Revenue], REMOVEFILTERS ( 'Calendar' ), 'Calendar'[Year] = _year )
        VAR _targetToDate = CALCULATE ( [Target to Date], REMOVEFILTERS ( 'Calendar' ), 'Calendar'[Year] = _year )
        VAR _targetRest =
            CALCULATE ( [Target], REMOVEFILTERS ( 'Calendar' ), 'Calendar'[Year] = _year, 'Calendar'[Has Actuals] = FALSE () )
        RETURN
            _actual + _targetRest * DIVIDE ( _actual, _targetToDate, 1 )""", EUR, "3 Target"),
    M("Full Year Target", """
        VAR _year = MAX ( 'Calendar'[Year] )
        RETURN CALCULATE ( [Target], REMOVEFILTERS ( 'Calendar' ), 'Calendar'[Year] = _year )""", EUR, "3 Target"),
    M("Forecast vs Full Year Target %", "DIVIDE ( [Full Year Forecast], [Full Year Target] )", PCT, "3 Target"),
    # ---------------------------------------------------------------- FX
    M("Net Revenue at Plan FX", "SUM ( Sales[Net EUR at Plan FX] )", EUR, "4 Currency",
      "Same invoices converted at Treasury's planning rate of the year."),
    M("FX Effect", "[Net Revenue] - [Net Revenue at Plan FX]", EUR, "4 Currency",
      "Positive = foreign currencies were stronger than planned (tailwind)."),
    M("FX Effect %", "DIVIDE ( [FX Effect], [Net Revenue at Plan FX] )", PCT, "4 Currency"),
    M("Revenue Share Foreign Currency", "DIVIDE ( CALCULATE ( [Net Revenue], Sales[Invoice Currency] <> \"EUR\" ), [Net Revenue] )",
      PCT, "4 Currency"),
    # ---------------------------------------------------------------- customers
    M("Active Customers", "CALCULATE ( DISTINCTCOUNT ( Customer[Customer ID] ), Sales )", INT, "5 Customers",
      "Distinct business customers (not SCD versions) with revenue in the period."),
    M("New Customers", """
        VAR _start = MIN ( 'Calendar'[Date] )
        VAR _customers = CALCULATETABLE ( VALUES ( Customer[Customer ID] ), Sales )
        RETURN
            COUNTROWS (
                FILTER (
                    _customers,
                    VAR _id = Customer[Customer ID]
                    VAR _firstOrder =
                        CALCULATE ( MIN ( Sales[Order Date] ), REMOVEFILTERS ( 'Calendar' ), REMOVEFILTERS ( Customer ), Customer[Customer ID] = _id )
                    RETURN _firstOrder >= _start
                )
            ) + 0""", INT, "5 Customers"),
    M("Customers at Risk", """
        -- bought in the 12 months before the last 90 days, but not in the last 90 days
        VAR _ref = CALCULATE ( MAX ( Sales[Order Date] ), REMOVEFILTERS ( 'Calendar' ) )
        VAR _before = CALCULATETABLE ( VALUES ( Customer[Customer ID] ), REMOVEFILTERS ( 'Calendar' ),
            DATESBETWEEN ( 'Calendar'[Date], _ref - 455, _ref - 90 ), Sales )
        VAR _recent = CALCULATETABLE ( VALUES ( Customer[Customer ID] ), REMOVEFILTERS ( 'Calendar' ),
            DATESBETWEEN ( 'Calendar'[Date], _ref - 89, _ref ), Sales )
        RETURN COUNTROWS ( EXCEPT ( _before, _recent ) ) + 0""", INT, "5 Customers",
      "Customers who ordered in the 12 months before the last 90 days but not since. Ignores the date slicer."),
    M("Revenue at Risk", """
        VAR _ref = CALCULATE ( MAX ( Sales[Order Date] ), REMOVEFILTERS ( 'Calendar' ) )
        VAR _before = CALCULATETABLE ( VALUES ( Customer[Customer ID] ), REMOVEFILTERS ( 'Calendar' ),
            DATESBETWEEN ( 'Calendar'[Date], _ref - 455, _ref - 90 ), Sales )
        VAR _recent = CALCULATETABLE ( VALUES ( Customer[Customer ID] ), REMOVEFILTERS ( 'Calendar' ),
            DATESBETWEEN ( 'Calendar'[Date], _ref - 89, _ref ), Sales )
        RETURN
            CALCULATE ( [Net Revenue], REMOVEFILTERS ( 'Calendar' ), DATESBETWEEN ( 'Calendar'[Date], _ref - 455, _ref - 90 ),
                TREATAS ( EXCEPT ( _before, _recent ), Customer[Customer ID] ) )""", EUR, "5 Customers",
      "Revenue of the customers at risk in their last active 12 months."),
    M("Revenue per Customer", "DIVIDE ( [Net Revenue], [Active Customers] )", EUR, "5 Customers"),
    # ---------------------------------------------------------------- report helpers
    M("Last Actual Date", "CALCULATE ( MAX ( Sales[Order Date] ), REMOVEFILTERS () )", "dd.mm.yyyy", "9 Report"),
    M("Overview Subtitle", """
        "Data through " & FORMAT ( [Last Actual Date], "dd.mm.yyyy" )
            & "  ·  Target achievement " & FORMAT ( [Target Achievement %], "0.0%" )
            & "  ·  Forecast " & FORMAT ( [Full Year Forecast] / 1000000, "€0.0" ) & "M of " & FORMAT ( [Full Year Target] / 1000000, "€0.0" ) & "M\"""",
      None, "9 Report"),
    M("Achievement Color", """
        VAR _a = [Target Achievement %]
        RETURN SWITCH ( TRUE (), ISBLANK ( _a ), "#9AA6AF", _a >= 1, "#0E6B63", _a >= 0.9, "#C98A0B", "#C0392B" )""",
      None, "9 Report", "Used for conditional formatting: green >= 100%, amber >= 90%, red below."),
]

TARGET = Table("Target", "fact_target", [
    Col("Country Code", "country_code", "string", hidden=True),
    Col("Month Start", "month_start", "dateTime", "mmm yyyy", hidden=True),
    Col("Date Key", "date_key", "int64", "0", hidden=True),
    Col("Target EUR", "target_eur", "double", EUR, hidden=True),
], desc="Monthly revenue budget per country (controlling), unpivoted from the wide Excel layout.")

FX = Table("FX Rate", "fact_fx_rate", [
    Col("Rate Date", "date", "dateTime", "dd.mm.yyyy", hidden=True),
    Col("Date Key", "date_key", "int64", "0", hidden=True),
    Col("Currency", "currency", "string"),
    Col("Rate per EUR", "rate_per_eur", "double", "0.0000", hidden=True),
], [
    M("Avg FX Rate", "AVERAGE ( 'FX Rate'[Rate per EUR] )", "0.0000", None, "Average ECB reference rate, units per 1 EUR."),
    M("FX Rate vs Start of Year %", """
        VAR _rate = [Avg FX Rate]
        VAR _start = CALCULATE ( [Avg FX Rate], STARTOFYEAR ( 'Calendar'[Date] ) )
        RETURN DIVIDE ( _start, _rate ) - 1""", PCT, None, "Positive = currency stronger against EUR than on 1 January."),
], desc="ECB euro reference rates, TARGET business days.")

DQ = Table("Data Quality", "fact_data_quality", [
    Col("Contract", "contract", "string"),
    Col("File", "file", "string"),
    Col("Check", "check", "string"),
    Col("Rows Checked", "rows_checked", "int64", INT, hidden=True),
    Col("Rows Failed", "rows_failed", "int64", INT, hidden=True),
    Col("Severity", "severity", "string"),
    Col("Action", "action", "string"),
    Col("Check Date", "check_date", "dateTime", "dd.mm.yyyy", hidden=True),
    Col("Run ID", "run_id", "string"),
    Col("Run Timestamp", "run_timestamp", "dateTime", "dd.mm.yyyy hh:nn", hidden=True),
    Col("Date Key", "date_key", "int64", "0", hidden=True),
], [
    M("Rows Quarantined", "CALCULATE ( SUM ( 'Data Quality'[Rows Failed] ), 'Data Quality'[Action] = \"quarantined\" ) + 0", INT),
    M("Values Auto-Fixed", "CALCULATE ( SUM ( 'Data Quality'[Rows Failed] ), 'Data Quality'[Action] = \"auto-fixed\" ) + 0", INT),
    M("Duplicates Removed", "CALCULATE ( SUM ( 'Data Quality'[Rows Failed] ), 'Data Quality'[Action] = \"deduplicated\" ) + 0", INT),
    M("ERP Rows Received", "CALCULATE ( SUM ( 'Data Quality'[Rows Checked] ), 'Data Quality'[Contract] = \"erp_order_lines\", 'Data Quality'[Check] = \"order_id: required\" )", INT),
    M("Quarantine Rate", "DIVIDE ( CALCULATE ( [Rows Quarantined], 'Data Quality'[Contract] = \"erp_order_lines\" ), [ERP Rows Received] )", "0.00%"),
    M("Contract Checks Run", "COUNTROWS ( 'Data Quality' )", INT),
    M("Last Pipeline Run", "CALCULATE ( MAX ( 'Data Quality'[Run Timestamp] ), REMOVEFILTERS () )", "dd.mm.yyyy hh:nn"),
    M("Data Age (days)", "DATEDIFF ( [Last Actual Date], TODAY (), DAY )", "0",
      None, "Days between the last loaded business date and today. Alert if > 3."),
], desc="One row per data-contract check per load. Appended on every pipeline run.")

SECURITY = Table("Security", "rls_user_country", [
    Col("User Principal", "user_principal", "string"),
    Col("Country Code", "country_code", "string"),
], hidden=True, desc="Maps user e-mails to the countries they may see (row-level security).")

SALES = Table("Sales", "fact_sales", SALES_COLS, SALES_MEASURES,
              desc="Order lines and returns at line grain. All measures live here, grouped in display folders.")

TABLES = [SALES, CAL, COUNTRY, CHANNEL, PRODUCT, CUSTOMER, TARGET, FX, DQ, SECURITY]

RELATIONSHIPS = [
    ("Sales", "Date Key", "Calendar", "Date Key"),
    ("Sales", "Customer SK", "Customer", "Customer SK"),
    ("Sales", "Product SK", "Product", "Product SK"),
    ("Sales", "Country Code", "Country", "Country Code"),
    ("Sales", "Channel Key", "Channel", "Channel Key"),
    ("Target", "Date Key", "Calendar", "Date Key"),
    ("Target", "Country Code", "Country", "Country Code"),
    ("FX Rate", "Date Key", "Calendar", "Date Key"),
    ("Data Quality", "Date Key", "Calendar", "Date Key"),
]

CALC_ITEMS = [
    ("Current", "SELECTEDMEASURE ()", None),
    ("MTD", "CALCULATE ( SELECTEDMEASURE (), DATESMTD ( 'Calendar'[Date] ) )", None),
    ("QTD", "CALCULATE ( SELECTEDMEASURE (), DATESQTD ( 'Calendar'[Date] ) )", None),
    ("YTD", "CALCULATE ( SELECTEDMEASURE (), DATESYTD ( 'Calendar'[Date] ) )", None),
    ("PY", "CALCULATE ( SELECTEDMEASURE (), SAMEPERIODLASTYEAR ( 'Calendar'[Date] ) )", None),
    ("YoY", """
        VAR _cy = SELECTEDMEASURE ()
        VAR _py = CALCULATE ( SELECTEDMEASURE (), SAMEPERIODLASTYEAR ( 'Calendar'[Date] ) )
        RETURN IF ( NOT ISBLANK ( _cy ) && NOT ISBLANK ( _py ), _cy - _py )""", None),
    ("YoY %", """
        VAR _cy = SELECTEDMEASURE ()
        VAR _py = CALCULATE ( SELECTEDMEASURE (), SAMEPERIODLASTYEAR ( 'Calendar'[Date] ) )
        RETURN IF ( NOT ISBLANK ( _cy ) && NOT ISBLANK ( _py ), DIVIDE ( _cy - _py, ABS ( _py ) ) )""", '"0.0%"'),
    ("Rolling 12M", "CALCULATE ( SELECTEDMEASURE (), DATESINPERIOD ( 'Calendar'[Date], MAX ( 'Calendar'[Date] ), -12, MONTH ) )", None),
]


def col_tmdl(t: Table, c: Col) -> str:
    out = []
    if c.desc:
        out.append(f"\t/// {c.desc}")
    out.append(f"\tcolumn {q(c.name)}")
    out.append(f"\t\tdataType: {c.dtype}")
    if c.hidden:
        out.append("\t\tisHidden")
    if c.key:
        out.append("\t\tisKey")
    if c.fmt:
        out.append(f"\t\tformatString: {c.fmt}")
    if c.folder:
        out.append(f"\t\tdisplayFolder: {c.folder}")
    if c.category:
        out.append(f"\t\tdataCategory: {c.category}")
    out.append(f"\t\tlineageTag: {tag(t.name, 'col', c.name)}")
    out.append(f"\t\tsummarizeBy: {c.summarize}")
    out.append(f"\t\tsourceColumn: {c.source}")
    if c.sort_by:
        out.append(f"\t\tsortByColumn: {q(c.sort_by)}")
    out.append("")
    out.append("\t\tannotation SummarizationSetBy = User")
    if c.dtype == "dateTime":
        out.append("")
        out.append("\t\tannotation UnderlyingDateTimeDataType = Date" if c.fmt and "hh" not in c.fmt else "\t\tannotation UnderlyingDateTimeDataType = DateTime")
    return "\n".join(out) + "\n"


def measure_tmdl(t: Table, m: Measure) -> str:
    out = []
    if m.desc:
        out.append(f"\t/// {m.desc}")
    expr = dedent(m.expr).strip("\n")
    if "\n" in expr:
        out.append(f"\tmeasure {q(m.name)} =")
        out.append(indent(expr, 3))
    else:
        out.append(f"\tmeasure {q(m.name)} = {expr.strip()}")
    if m.fmt:
        out.append(f"\t\tformatString: {m.fmt}")
    if m.folder:
        out.append(f"\t\tdisplayFolder: {m.folder}")
    out.append(f"\t\tlineageTag: {tag(t.name, 'measure', m.name)}")
    return "\n".join(out) + "\n"


def table_tmdl(t: Table) -> str:
    out = []
    if t.desc:
        out.append(f"/// {t.desc}")
    out.append(f"table {q(t.name)}")
    if t.hidden:
        out.append("\tisHidden")
    if t.data_category:
        out.append(f"\tdataCategory: {t.data_category}")
    out.append(f"\tlineageTag: {tag(t.name)}")
    out.append("")
    for m in t.measures:
        out.append(measure_tmdl(t, m))
    for c in t.cols:
        out.append(col_tmdl(t, c))
    types = {"string": "type text", "int64": "Int64.Type", "double": "type number", "dateTime": "type date",
             "boolean": "type logical"}
    transforms = ", ".join(
        "{" + f'"{c.source}", ' + ("type datetime" if c.dtype == "dateTime" and c.fmt and "hh" in c.fmt else types[c.dtype]) + "}"
        for c in t.cols)
    out.append(f"\tpartition {q(t.name)} = m")
    out.append("\t\tmode: import")
    out.append("\t\tsource =")
    out.append(indent(f"""
        let
            Source = fnReadGold("{t.gold}"),
            Typed = Table.TransformColumnTypes(Source, {{{transforms}}})
        in
            Typed""", 4))
    out.append("")
    out.append("\tannotation PBI_ResultType = Table")
    return "\n".join(out) + "\n"


def calc_group_tmdl() -> str:
    t = "Time Calculation"
    out = ["/// Calculation group: apply any measure as MTD, QTD, YTD, PY, YoY, YoY % or rolling 12 months.",
           f"table {q(t)}", f"\tlineageTag: {tag(t)}", "", "\tcalculationGroup", "\t\tprecedence: 10", ""]
    for i, (name, expr, fmt) in enumerate(CALC_ITEMS):
        e = dedent(expr).strip("\n")
        if "\n" in e:
            out.append(f"\t\tcalculationItem {q(name)} =")
            out.append(indent(e, 4))
        else:
            out.append(f"\t\tcalculationItem {q(name)} = {e}")
        out.append(f"\t\t\tordinal: {i}")
        if fmt:
            out.append("")
            out.append(f"\t\t\tformatStringDefinition = {fmt}")
        out.append("")
    out += [f"\tcolumn {q(t)}", "\t\tdataType: string", f"\t\tlineageTag: {tag(t, 'col', 'name')}", "\t\tsummarizeBy: none",
            "\t\tsourceColumn: Name", "\t\tsortByColumn: Ordinal", "", "\t\tannotation SummarizationSetBy = User", "",
            "\tcolumn Ordinal", "\t\tdataType: int64", "\t\tisHidden", "\t\tformatString: 0",
            f"\t\tlineageTag: {tag(t, 'col', 'ordinal')}", "\t\tsummarizeBy: sum", "\t\tsourceColumn: Ordinal", "",
            "\t\tannotation SummarizationSetBy = User", ""]
    return "\n".join(out)


def build_model(github_user: str, repo: str, branch: str) -> None:
    sm = OUT / f"{NAME}.SemanticModel"
    d = sm / "definition"
    (d / "tables").mkdir(parents=True)
    (d / "roles").mkdir()
    (sm / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("logical", "model")}}, indent=2))
    (sm / "definition.pbism").write_text(json.dumps({"version": "4.0", "settings": {}}, indent=2))
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1601\n\n")
    tables = [t.name for t in TABLES] + ["Time Calculation"]
    model = ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3", "\tdiscourageImplicitMeasures",
             "\tsourceQueryCulture: en-US", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
             "annotation __PBI_TimeIntelligenceEnabled = 0", "",
             "annotation PBI_QueryOrder = " + json.dumps(["GoldFolder", "fnReadGold"] + [t.name for t in TABLES]), "",
             'annotation PBI_ProTooling = ["DevMode","CalcGroup","TMDLView_Desktop"]', ""]
    model += [f"ref table {q(n)}" for n in tables] + [""]
    model += ["ref role 'Country Manager'", "ref role 'DACH Sales'", ""]
    (d / "model.tmdl").write_text("\n".join(model))
    url = f"https://raw.githubusercontent.com/{github_user}/{repo}/{branch}/data/gold/"
    (d / "expressions.tmdl").write_text(dedent(f'''\
        /// Where the gold Parquet files live. Default: your GitHub repo (updated daily by GitHub Actions).
        /// For local work set it to a folder path ending with a backslash, e.g. C:\\\\repos\\\\fabric-export-analytics\\\\data\\\\gold\\\\
        expression GoldFolder = "{url}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]
        \tlineageTag: {tag("expr", "GoldFolder")}

        \tannotation PBI_ResultType = Text

        /// Reads one gold table (Parquet) from the web or from a local folder.
        expression fnReadGold =
        \t\t(tableName as text) as table =>
        \t\tlet
        \t\t    Content = if Text.StartsWith(GoldFolder, "http")
        \t\t        then Web.Contents(GoldFolder, [RelativePath = tableName & ".parquet"])
        \t\t        else File.Contents(GoldFolder & tableName & ".parquet"),
        \t\t    Data = Parquet.Document(Binary.Buffer(Content))
        \t\tin
        \t\t    Data
        \tlineageTag: {tag("expr", "fnReadGold")}

        \tannotation PBI_ResultType = Function

        '''))
    for t in TABLES:
        (d / "tables" / f"{t.name}.tmdl").write_text(table_tmdl(t))
    (d / "tables" / "Time Calculation.tmdl").write_text(calc_group_tmdl())
    rel = []
    for ft, fc, tt, tc in RELATIONSHIPS:
        rel += [f"relationship {tag('rel', ft, fc, tt)}", f"\tfromColumn: {q(ft)}.{q(fc)}", f"\ttoColumn: {q(tt)}.{q(tc)}", ""]
    (d / "relationships.tmdl").write_text("\n".join(rel))
    (d / "roles" / "Country Manager.tmdl").write_text(dedent(f"""\
        /// Dynamic RLS: each country manager sees only the countries mapped to their e-mail in the Security table.
        role 'Country Manager'
        \tmodelPermission: read

        \ttablePermission Country =
        \t\t\t'Country'[Country Code]
        \t\t\t    IN CALCULATETABLE (
        \t\t\t        VALUES ( 'Security'[Country Code] ),
        \t\t\t        'Security'[User Principal] = USERPRINCIPALNAME ()
        \t\t\t    )

        \tannotation PBI_Id = {tag('role', 'cm').replace('-', '')}

        """))
    (d / "roles" / "DACH Sales.tmdl").write_text(dedent(f"""\
        /// Static RLS example: DACH team sees Germany and Switzerland.
        role 'DACH Sales'
        \tmodelPermission: read

        \ttablePermission Country = 'Country'[Region] = "DACH"

        \tannotation PBI_Id = {tag('role', 'dach').replace('-', '')}

        """))


# =========================================================================== report (PBIR)

def lit(v) -> dict:
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, (int, float)):
        s = f"{v}D"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": s}}}


def measure(entity: str, prop: str) -> dict:
    return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
            "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}


def column(entity: str, prop: str, active: bool = True) -> dict:
    d = {"field": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
         "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if active:
        d["active"] = True
    return d


class Page:
    def __init__(self, key: str, title: str):
        self.name = tag("page", key).replace("-", "")[:20]
        self.title = title
        self.visuals: list[dict] = []
        self.z = 0

    def add(self, vtype: str, x, y, w, h, roles: dict | None = None, title: str | None = None,
            objects: dict | None = None, sort: dict | None = None, container: dict | None = None):
        self.z += 1000
        name = tag("visual", self.name, str(self.z)).replace("-", "")[:20]
        visual: dict = {"visualType": vtype}
        if roles:
            visual["query"] = {"queryState": {r: {"projections": p} for r, p in roles.items()}}
            if sort:
                visual["query"]["sortDefinition"] = sort
        if objects:
            visual["objects"] = objects
        vco = dict(container or {})
        if title:
            vco["title"] = [{"properties": {"show": lit(True), "text": lit(title), "fontSize": lit(11), "bold": lit(True)}}]
        vco.setdefault("dropShadow", [{"properties": {"show": lit(False)}}])
        vco.setdefault("background", [{"properties": {"show": lit(True), "color": {"solid": {"color": lit("#FFFFFF")}}, "transparency": lit(0)}}])
        vco.setdefault("border", [{"properties": {"show": lit(True), "color": {"solid": {"color": lit("#DDE4E8")}}, "radius": lit(8)}}])
        visual["visualContainerObjects"] = vco
        visual["drillFilterOtherVisuals"] = True
        self.visuals.append({
            "$schema": f"{SCHEMA}/visualContainer/2.0.0/schema.json",
            "name": name,
            "position": {"x": x, "y": y, "z": self.z, "height": h, "width": w, "tabOrder": self.z},
            "visual": visual,
        })

    def text(self, x, y, w, h, runs: list[tuple[str, dict]]):
        self.z += 1000
        name = tag("visual", self.name, str(self.z)).replace("-", "")[:20]
        self.visuals.append({
            "$schema": f"{SCHEMA}/visualContainer/2.0.0/schema.json",
            "name": name,
            "position": {"x": x, "y": y, "z": self.z, "height": h, "width": w, "tabOrder": self.z},
            "visual": {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [
                {"textRuns": [{"value": v, "textStyle": s} for v, s in runs]}]}}]},
                "drillFilterOtherVisuals": True},
        })


def card(p: Page, x, y, w, entity, prop, label):
    p.add("card", x, y, w, 92, {"Values": [measure(entity, prop)]},
          objects={"labels": [{"properties": {"fontSize": lit(22), "color": {"solid": {"color": lit("#13202A")}}}}],
                   "categoryLabels": [{"properties": {"show": lit(True), "fontSize": lit(10)}}]},
          container={"title": [{"properties": {"show": lit(False)}}]})


def header(p: Page, title: str, subtitle_measure: bool = False):
    p.text(24, 14, 760, 44, [(title, {"fontFamily": "Segoe UI Semibold", "fontSize": "20pt", "color": "#13202A"})])
    if subtitle_measure:
        p.add("card", 24, 54, 820, 30, {"Values": [measure("Sales", "Overview Subtitle")]},
              objects={"labels": [{"properties": {"fontSize": lit(10), "color": {"solid": {"color": lit("#566571")}}}}],
                       "categoryLabels": [{"properties": {"show": lit(False)}}]},
              container={"title": [{"properties": {"show": lit(False)}}], "border": [{"properties": {"show": lit(False)}}],
                         "background": [{"properties": {"show": lit(False)}}]})


def slicer(p: Page, x, y, w, entity, prop, title):
    p.add("slicer", x, y, w, 56, {"Values": [column(entity, prop)]}, title=title,
          objects={"data": [{"properties": {"mode": lit("Dropdown")}}]})


def build_pages() -> list[Page]:
    pages = []
    # ------------------------------------------------------------------ 1 overview
    p = Page("overview", "Executive Overview")
    header(p, "Sales performance vs target", subtitle_measure=True)
    slicer(p, 880, 14, 180, "Calendar", "Year", "Year")
    slicer(p, 1076, 14, 180, "Country", "Region", "Region")
    for i, (m, label) in enumerate([("Net Revenue", "Net revenue"), ("Target Achievement %", "Target achievement"),
                                    ("Gross Margin %", "Gross margin"), ("FX Effect", "FX effect vs plan"),
                                    ("Full Year Forecast", "Full-year forecast")]):
        card(p, 24 + i * 248, 96, 236, "Sales", m, label)
    p.add("lineClusteredColumnComboChart", 24, 200, 760, 250,
          {"Category": [column("Calendar", "Year Month")], "Y": [measure("Sales", "Net Revenue")],
           "Y2": [measure("Sales", "Target")]}, title="Net revenue vs target by month")
    p.add("clusteredBarChart", 796, 200, 460, 250,
          {"Category": [column("Country", "Country")], "Y": [measure("Sales", "Target Achievement %")]},
          title="Target achievement by country",
          sort={"sort": [{"field": measure("Sales", "Target Achievement %")["field"], "direction": "Ascending"}]},
          objects={"dataPoint": [{"properties": {"fill": {"solid": {"color": {"expr": {"Measure": {
              "Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": "Achievement Color"}}}}}}}]})
    p.add("pivotTable", 24, 462, 1232, 240,
          {"Rows": [column("Country", "Region"), column("Country", "Country", active=False)],
           "Columns": [column("Time Calculation", "Time Calculation")],
           "Values": [measure("Sales", "Net Revenue")]},
          title="Net revenue by region · calculation group (MTD, YTD, PY, YoY, YoY %, rolling 12M)")
    pages.append(p)

    # ------------------------------------------------------------------ 2 customers & margin
    p = Page("customers", "Customers & Margin")
    header(p, "Who buys, and what it earns")
    slicer(p, 880, 14, 180, "Calendar", "Year", "Year")
    slicer(p, 1076, 14, 180, "Customer", "Segment", "Segment")
    for i, (m, label) in enumerate([("Active Customers", "Active customers"), ("New Customers", "New customers"),
                                    ("Customers at Risk", "Customers at risk (90 days)"), ("Revenue at Risk", "Revenue at risk"),
                                    ("Discount %", "Average discount")]):
        card(p, 24 + i * 248, 80, 236, "Sales", m, label)
    p.add("clusteredBarChart", 24, 186, 400, 250,
          {"Category": [column("Customer", "Segment")], "Y": [measure("Sales", "Gross Margin %")]},
          title="Gross margin by segment",
          sort={"sort": [{"field": measure("Sales", "Gross Margin %")["field"], "direction": "Ascending"}]})
    p.add("clusteredBarChart", 436, 186, 400, 250,
          {"Category": [column("Customer", "Segment")], "Y": [measure("Sales", "Discount %")]},
          title="Average discount by segment",
          sort={"sort": [{"field": measure("Sales", "Discount %")["field"], "direction": "Descending"}]})
    p.add("clusteredColumnChart", 848, 186, 408, 250,
          {"Category": [column("Product", "Category")], "Y": [measure("Sales", "Gross Margin")]},
          title="Gross margin by product category",
          sort={"sort": [{"field": measure("Sales", "Gross Margin")["field"], "direction": "Descending"}]})
    p.add("tableEx", 24, 448, 1232, 256,
          {"Values": [column("Customer", "Customer"), column("Customer", "Segment"), column("Customer", "Sales Rep"),
                      measure("Sales", "Net Revenue"), measure("Sales", "Gross Margin %"), measure("Sales", "Discount %"),
                      measure("Sales", "Orders"), measure("Sales", "Avg Order Value")]},
          title="Top customers",
          sort={"sort": [{"field": measure("Sales", "Net Revenue")["field"], "direction": "Descending"}]})
    pages.append(p)

    # ------------------------------------------------------------------ 3 FX
    p = Page("fx", "Currency & Pricing")
    header(p, "Currency effects on export revenue")
    slicer(p, 1076, 14, 180, "Calendar", "Year", "Year")
    for i, (m, label) in enumerate([("Net Revenue at Plan FX", "Revenue at planning rates"), ("FX Effect", "FX effect"),
                                    ("FX Effect %", "FX effect %"), ("Revenue Share Foreign Currency", "Share invoiced in foreign currency")]):
        card(p, 24 + i * 310, 80, 298, "Sales", m, label)
    p.add("clusteredBarChart", 24, 186, 500, 518,
          {"Category": [column("Sales", "Invoice Currency")], "Y": [measure("Sales", "FX Effect")]},
          title="FX effect by invoice currency (actual ECB rate vs planning rate)",
          sort={"sort": [{"field": measure("Sales", "FX Effect")["field"], "direction": "Ascending"}]})
    p.add("lineChart", 536, 186, 720, 518,
          {"Category": [column("Calendar", "Month Start")], "Y": [measure("FX Rate", "FX Rate vs Start of Year %")],
           "Series": [column("FX Rate", "Currency")]},
          title="Currency strength vs 1 January (positive = stronger than EUR at year start)")
    pages.append(p)

    # ------------------------------------------------------------------ 4 data quality
    p = Page("dq", "Data Quality")
    header(p, "Pipeline health and data contracts")
    for i, (e, m, label) in enumerate([("Data Quality", "Last Pipeline Run", "Last pipeline run"),
                                       ("Data Quality", "Data Age (days)", "Data age (days)"),
                                       ("Data Quality", "Quarantine Rate", "Quarantine rate (ERP)"),
                                       ("Data Quality", "Values Auto-Fixed", "Values auto-fixed"),
                                       ("Data Quality", "Duplicates Removed", "Duplicates removed")]):
        card(p, 24 + i * 248, 80, 236, e, m, label)
    p.add("clusteredColumnChart", 24, 186, 760, 250,
          {"Category": [column("Calendar", "Year Month")], "Y": [measure("Data Quality", "Rows Quarantined")]},
          title="Rows quarantined per month")
    p.add("clusteredBarChart", 796, 186, 460, 250,
          {"Category": [column("Data Quality", "Check")], "Y": [measure("Data Quality", "Rows Quarantined")]},
          title="Quarantine reasons",
          sort={"sort": [{"field": measure("Data Quality", "Rows Quarantined")["field"], "direction": "Descending"}]})
    p.add("tableEx", 24, 448, 1232, 256,
          {"Values": [column("Data Quality", "Contract"), column("Data Quality", "Check"), column("Data Quality", "Severity"),
                      column("Data Quality", "Action"), measure("Data Quality", "Rows Quarantined"),
                      measure("Data Quality", "Values Auto-Fixed"), measure("Data Quality", "Duplicates Removed")]},
          title="All contract checks")
    pages.append(p)
    return pages


def build_report() -> None:
    rp = OUT / f"{NAME}.Report"
    d = rp / "definition"
    (d / "pages").mkdir(parents=True)
    (rp / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("logical", "report")}}, indent=2))
    (rp / "definition.pbir").write_text(json.dumps({"version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2))
    (d / "version.json").write_text(json.dumps({"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}, indent=2))
    base_theme = "CY24SU10"
    res = rp / "StaticResources"
    (res / "SharedResources" / "BaseThemes").mkdir(parents=True)
    (res / "RegisteredResources").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "assets" / f"{base_theme}.json", res / "SharedResources" / "BaseThemes" / f"{base_theme}.json")
    theme = {
        "name": "Valdora",
        "dataColors": ["#0E6B63", "#2A78D6", "#C98A0B", "#7A4FBF", "#C0392B", "#5FA8A0", "#E87BA4", "#566571"],
        "background": "#FFFFFF", "foreground": "#13202A", "tableAccent": "#0E6B63",
        "good": "#0E6B63", "neutral": "#C98A0B", "bad": "#C0392B",
        "textClasses": {"title": {"fontFace": "Segoe UI Semibold", "fontSize": 11, "color": "#13202A"},
                        "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 22, "color": "#13202A"},
                        "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": "#566571"}},
        "visualStyles": {"page": {"*": {"background": [{"color": {"solid": {"color": "#F3F6F7"}}, "transparency": 0}]}}},
    }
    (res / "RegisteredResources" / "Valdora.json").write_text(json.dumps(theme, indent=2))
    (d / "report.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/report/1.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": base_theme, "reportVersionAtImport": "5.59", "type": "SharedResources"},
            "customTheme": {"name": "Valdora.json", "reportVersionAtImport": "5.59", "type": "RegisteredResources"}},
        "layoutOptimization": "None",
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": base_theme, "path": f"BaseThemes/{base_theme}.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": "Valdora.json", "path": "Valdora.json", "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True, "useEnhancedTooltips": True},
    }, indent=2))
    pages = build_pages()
    (d / "pages" / "pages.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name}, indent=2))
    for p in pages:
        pd = d / "pages" / p.name
        (pd / "visuals").mkdir(parents=True)
        (pd / "page.json").write_text(json.dumps({
            "$schema": f"{SCHEMA}/page/2.0.0/schema.json", "name": p.name, "displayName": p.title,
            "displayOption": "FitToPage", "height": 720, "width": 1280}, indent=2))
        for v in p.visuals:
            (pd / "visuals" / v["name"]).mkdir()
            (pd / "visuals" / v["name"] / "visual.json").write_text(json.dumps(v, indent=2, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github-user", default="Dhrukumar23")
    ap.add_argument("--repo", default="fabric-export-analytics")
    ap.add_argument("--branch", default="main")
    a = ap.parse_args()
    for p in (OUT / f"{NAME}.SemanticModel", OUT / f"{NAME}.Report"):
        shutil.rmtree(p, ignore_errors=True)
    OUT.mkdir(exist_ok=True)
    build_model(a.github_user, a.repo, a.branch)
    build_report()
    (OUT / f"{NAME}.pbip").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True}}, indent=2))
    (OUT / ".gitignore").write_text("**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")
    n_measures = sum(len(t.measures) for t in TABLES)
    print(f"PBIP written to {OUT}: {len(TABLES) + 1} tables, {n_measures} measures, {len(CALC_ITEMS)} calculation items")


if __name__ == "__main__":
    main()
