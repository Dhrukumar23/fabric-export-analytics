# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "jupyter",
# META     "jupyter_kernel_name": "python3.11"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse_name": "ExportLake"
# META     }
# META   }
# META }

# MARKDOWN ********************

# # ExportLake · daily load
# Runs the same pipeline as GitHub Actions, but writes the star schema as **Delta tables into this
# Lakehouse** (`/lakehouse/default/Tables`), ready for a Direct Lake semantic model.
# Schedule: Run → Schedule → daily 06:30.

# CELL ********************

%pip install -q "exportlake @ git+https://github.com/Dhrukumar23/fabric-export-analytics.git"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

from datetime import date, timedelta
from pathlib import Path

from exportlake.generator import generate
from exportlake.pipeline.run import run

ROOT = Path("/lakehouse/default/Files/exportlake")          # landing zone, state and gold parquet
TABLES = Path("/lakehouse/default/Tables")                  # managed Delta tables
until = date.today() - timedelta(days=1)

generate(date(2024, 1, 1), until, ROOT / "data" / "landing")   # simulated source systems drop their files
summary = run(ROOT, until=until, allow_network=True, write_lake=True, lake_tables=TABLES)
print(summary)

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

# Fail the scheduled run (and alert) if contracts quarantined too much data
import polars as pl
dq = pl.read_parquet(ROOT / "data" / "gold" / "fact_data_quality.parquet").filter(pl.col("run_id") == summary.run_id)
quarantined = dq.filter(pl.col("action") == "quarantined")["rows_failed"].sum()
assert quarantined < max(50, 0.02 * max(1, summary.rows_loaded)), f"too many rows quarantined: {quarantined}"

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }
