"""Command line entry point.

  exportlake daily       # what the scheduled GitHub Action runs every morning
  exportlake bootstrap   # rebuild everything from 2024-01-01 (first run / disaster recovery)
  exportlake run         # only process files already in the landing zone
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import HISTORY_START, Paths, project_root


def _date(s: str) -> date:
    return date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="exportlake")
    ap.add_argument("command", choices=["daily", "bootstrap", "run", "generate", "digest", "dashboard"])
    ap.add_argument("--root", type=Path, default=project_root())
    ap.add_argument("--until", type=_date, default=None, help="last business date to load (default: yesterday)")
    ap.add_argument("--offline", action="store_true", help="do not call the ECB API")
    ap.add_argument("--no-lake", action="store_true", help="skip writing Delta tables")
    args = ap.parse_args(argv)

    paths = Paths(args.root).ensure()
    until = args.until or (date.today() - timedelta(days=1))

    if args.command in ("daily", "bootstrap", "generate"):
        from .generator import generate
        start = HISTORY_START
        if args.command == "daily":
            wm = paths.state / "watermark.json"
            if wm.exists() and json.loads(wm.read_text()).get("last_business_date"):
                start = date.fromisoformat(json.loads(wm.read_text())["last_business_date"]) + timedelta(days=1)
        if args.command == "bootstrap":
            import shutil
            for sub in ("gold", "state", "quarantine", "landing", "lakehouse"):
                shutil.rmtree(args.root / "data" / sub, ignore_errors=True)
            paths.ensure()
        counts = generate(start, until, paths.landing)
        print(f"generated source extracts {start}..{until}: {counts}")
        if args.command == "generate":
            return 0

    if args.command in ("daily", "bootstrap", "run"):
        from .pipeline.run import run
        s = run(args.root, until=until, allow_network=not args.offline, write_lake=not args.no_lake)
        print(json.dumps({"run_id": s.run_id, "days": len(s.days_processed), "rows_loaded": s.rows_loaded,
                          "rows_quarantined": s.rows_quarantined, "fx_source": s.fx_source,
                          "customers": s.customer_stats, "warnings": len(s.warnings)}, indent=2))

    if args.command in ("daily", "bootstrap", "digest"):
        from .insights import write_digest
        md = write_digest(paths)
        summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_file:
            with open(summary_file, "a", encoding="utf-8") as fh:
                fh.write(md)
        print(md[:1500])

    if args.command in ("daily", "bootstrap", "dashboard"):
        from .dashboard import build_dashboard
        out = build_dashboard(paths, args.root / "docs" / "index.html")
        print(f"dashboard written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
