# Valdora Export Analytics

**An automated sales analytics platform for a German B2B exporter, from raw ERP files to a Power BI model, refreshed every day without anyone touching it.**

> Valdora Components GmbH is a **fictional** company. All data is simulated, but it is simulated the way real systems deliver it: German number formats, Windows-1252 encoding, re-exported duplicates, missing customer numbers, late CRM records, and real ECB exchange rates.

| | |
|---|---|
| **Live dashboard** | `https://dhrukumar23.github.io/fabric-export-analytics/` (after the first workflow run) |
| **Power BI** | `powerbi/ValdoraSales.pbip` (PBIP with TMDL model and PBIR report) |
| **Runs** | every morning at 05:15 UTC via GitHub Actions |
| **Built by** | Dhrukumar Valand |

---

## The business problem

Valdora sells hydraulics, pneumatics, sensors and drive parts to **9 countries in 7 currencies**. Sales, controlling and management each pull their own Excel extracts, so:

- nobody agrees on revenue, because exports are converted at different rates;
- the budget lives in a wide Excel sheet that cannot be compared with daily ERP data;
- bad rows (missing customer numbers, unknown articles) silently disappear from reports;
- when a customer moves from *SME* to *Key Account*, last year's revenue moves with them.

## What this project delivers

```
 Source systems            Pipeline (Python · Fabric notebook)                  Serving
 ───────────────           ─────────────────────────────────────                ─────────────────────
 ERP orders  (CSV, ;, de-DE) ─┐                                                 ┌─ Power BI semantic model
 CRM API     (JSON delta)    ─┤  data contracts ─► star schema ─► Delta/Parquet ┤   (TMDL, 43 DAX measures,
 Product master (CSV)        ─┤  · cleanse       · SCD Type 2      in OneLake    │    calc group, RLS)
 Budget      (wide Excel)    ─┤  · type-cast     · inferred members              ├─ Power BI report (PBIR, 4 pages)
 ECB FX rates (SDMX API)     ─┘  · quarantine    · incremental                   ├─ Web dashboard (GitHub Pages)
                                 · DQ log        · idempotent                    └─ Daily KPI digest (Markdown)
```

### 1 · Data contracts instead of "hope the file is fine"
Every source has a versioned YAML contract (`src/exportlake/contracts/`) with types, formats, allowed values, keys and business rules. Each row is either **auto-fixed** (trimming, upper-casing), **quarantined with a reason**, or loaded. Nothing disappears silently, and every check is logged.

### 2 · A proper star schema
| Table | Grain | Notes |
|---|---|---|
| `fact_sales` | order line | EUR at the ECB rate of the order date, plus EUR at the planning rate |
| `fact_target` | country × month | budget unpivoted from the wide controlling sheet |
| `fact_fx_rate` | currency × business day | live ECB reference rates |
| `fact_data_quality` | check × file × run | appended on every run |
| `dim_customer` | **SCD Type 2** | segment and sales rep keep their history; late CRM records become *inferred members* that are completed later under the same key |
| `dim_date`, `dim_country`, `dim_product`, `dim_channel` | | conformed dimensions |

### 3 · Power BI as code (PBIP / TMDL)
`tools/build_pbip.py` generates the whole Power BI project, so every measure is reviewed in Git like code.

- **43 DAX measures** in display folders: revenue, margin, targets, currency, customers, data quality
- **Working-day phased targets**: the monthly budget is spread over the selected days by working days, so *month-to-date vs target* is fair on any date
- **Full-year forecast** = actuals + remaining budget × achievement rate reached so far
- **FX effect**: revenue at actual ECB rates minus revenue at Treasury's planning rates (Q4 average of the prior year)
- **Customers at risk / revenue at risk**: bought in the 12 months before the last 90 days, but not since
- **Calculation group** *Time Calculation*: Current, MTD, QTD, YTD, PY, YoY, YoY %, Rolling 12M for any measure
- **Row-level security**: dynamic *Country Manager* role (`USERPRINCIPALNAME()` → Security table) and a static *DACH Sales* role
- **4 report pages**: Executive Overview, Customers & Margin, Currency & Pricing, Data Quality

### 4 · Everything automated
| Workflow | What it does |
|---|---|
| `daily.yml` (05:15 UTC) | loads new source files → fetches ECB rates → runs contracts → updates the star schema → tests → commits the gold layer → publishes the web dashboard → deploys to Microsoft Fabric (if secrets are set) |
| `ci.yml` (every push) | static checks · 12 unit and end-to-end tests · checks that the PBIP matches its generator · semantic-model best-practice lint · validates every report file against Microsoft's PBIR JSON schemas |

### 5 · Microsoft Fabric ready
`fabric/workspace/` contains a Lakehouse and a Python notebook (`ExportLake_Daily`) that runs the same pipeline inside Fabric and writes **Delta tables to OneLake** (`/lakehouse/default/Tables`). `fabric/deploy.py` uses Microsoft's **fabric-cicd** library to deploy the Lakehouse, notebook, semantic model and report to a workspace from GitHub Actions.

---

## Results on the current data

| | |
|---|---|
| Order lines loaded | ~47,000 (2024-01 → today, growing daily) |
| Contract checks run | 31,000+ |
| Rows quarantined with a reason | 235 |
| Values auto-fixed | 768 |
| Duplicate lines removed | 406 |
| Late-arriving customers handled | 14 inferred members, all completed |

**Insights the model surfaces:**
- **Distributors and Key Accounts carry the lowest gross margin (36–38%)** because of 11–20% average discounts; SME workshops earn 47% at a 2% discount.
- **Every European market is below its year-to-date target**, while the **USA (≈120%) and Japan (≈104%)**, both entered in 2025, beat their budgets.
- **PLN and JPY weakness cost about €210k** against planning rates in 2026, partly offset by a stronger CHF and SEK.

## Run it yourself

```bash
pip install -e ".[dev]"
exportlake bootstrap          # build everything from 2024-01-01 (≈30 s)
exportlake daily              # what the scheduler runs every morning
pytest -q                     # 12 tests
python tools/build_pbip.py    # regenerate the Power BI project
python tools/lint_model.py    # semantic-model best-practice checks
```

Open `powerbi/ValdoraSales.pbip` in Power BI Desktop. The model reads the gold Parquet files from this GitHub repository. To work offline, set the `GoldFolder` parameter to your local `data\gold\` folder.

## Set up the automation (once)

1. Push this folder to `github.com/Dhrukumar23/fabric-export-analytics` (public).
2. **Settings → Pages → Source: GitHub Actions.**
3. **Actions → Daily data load → Run workflow** (tick *bootstrap* the first time).
4. Optional, for Microsoft Fabric: add the secrets `FABRIC_WORKSPACE_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET` (a service principal with Contributor access to the workspace).

## Repository map

```
src/exportlake/
  contracts/        YAML data contracts (one per source)
  generator/        simulation of ERP, CRM, budget and FX source systems
  sources/fx.py     ECB Data Portal client with offline fallback
  quality.py        contract engine: cleanse, cast, validate, quarantine, log
  pipeline/         ingest, SCD2 customer dimension, incremental star-schema build
  insights.py       daily KPI digest with anomaly detection (robust z-score)
  dashboard.py      static web dashboard for GitHub Pages
powerbi/            PBIP: ValdoraSales.SemanticModel (TMDL) + ValdoraSales.Report (PBIR)
fabric/             Fabric Lakehouse + notebook + fabric-cicd deployment
tools/              PBIP generator, model linter, PBIR schema validator
tests/              unit and end-to-end tests
data/gold/          star schema (Parquet), committed daily by the workflow
reports/            daily digest and metrics JSON
docs/               web dashboard (GitHub Pages)
```
