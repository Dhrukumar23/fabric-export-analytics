"""Best-practice checks for the TMDL semantic model, run in CI on every push.

A small, dependency-free subset of the rules Tabular Editor's Best Practice
Analyzer applies, plus checks that catch broken references before Power BI does.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = next((ROOT / "powerbi").glob("*.SemanticModel")) / "definition"

NAME = r"(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_]*)"


def unq(s: str) -> str:
    return s[1:-1].replace("''", "'") if s.startswith("'") else s


def parse() -> dict:
    tables: dict[str, dict] = {}
    for f in sorted((MODEL / "tables").glob("*.tmdl")):
        text = f.read_text(encoding="utf-8")
        t = unq(re.search(rf"^table ({NAME})", text, re.M).group(1))
        cols = {unq(m.group(1)): m.start() for m in re.finditer(rf"^\tcolumn ({NAME})", text, re.M)}
        measures = {}
        for m in re.finditer(rf"^(\t///[^\n]*\n)?\tmeasure ({NAME}) =(.*?)(?=^\t\S|\Z)", text, re.M | re.S):
            body = m.group(3)
            measures[unq(m.group(2))] = {"desc": bool(m.group(1)), "body": body,
                                        "fmt": "formatString:" in body, "folder": "displayFolder:" in body}
        hidden_cols = set(re.findall(rf"^\tcolumn ({NAME})\n\t\tdataType: \w+\n\t\tisHidden", text, re.M))
        tables[t] = {"cols": cols, "measures": measures, "hidden": {unq(h) for h in hidden_cols},
                     "text": text, "file": f.name, "calc_group": "calculationGroup" in text,
                     "date_table": "dataCategory: Time" in text}
    return tables


def main() -> int:
    tables = parse()
    issues: list[str] = []
    all_measures = {m for t in tables.values() for m in t["measures"]}
    for tname, t in tables.items():
        for mname, m in t["measures"].items():
            where = f"{tname}[{mname}]"
            if not m["fmt"] and "Subtitle" not in mname and "Color" not in mname:
                issues.append(f"{where}: measure has no format string")
            if not m["folder"] and tname == "Sales":
                issues.append(f"{where}: measure has no display folder")
            if re.search(r"(?<![A-Z_])(?:\w+\s*/\s*\w+)", re.sub(r"'[^']*'|\"[^\"]*\"|\[[^\]]*\]|--.*", "", m["body"])) and "DIVIDE" not in m["body"]:
                if re.search(r"\]\s*/\s*\[", m["body"]):
                    issues.append(f"{where}: use DIVIDE() instead of '/' between measures")
            for ref_t, ref_c in re.findall(rf"({NAME})\[([^\]]+)\]", m["body"]):
                rt = unq(ref_t)
                if rt in tables and ref_c not in tables[rt]["cols"] and ref_c not in tables[rt]["measures"]:
                    issues.append(f"{where}: unknown column {rt}[{ref_c}]")
            for ref in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", m["body"]):
                if ref not in all_measures and not any(ref in tt["cols"] for tt in tables.values()):
                    issues.append(f"{where}: unknown measure [{ref}]")
    rel = (MODEL / "relationships.tmdl").read_text(encoding="utf-8")
    for side in re.findall(rf"(?:fromColumn|toColumn): ({NAME})\.({NAME})", rel):
        t, c = unq(side[0]), unq(side[1])
        if t not in tables or c not in tables[t]["cols"]:
            issues.append(f"relationship references missing column {t}[{c}]")
    from_cols = re.findall(rf"fromColumn: ({NAME})\.({NAME})", rel)
    for t, c in from_cols:
        if unq(c) not in tables[unq(t)]["hidden"] and unq(t) not in ("Target",):
            issues.append(f"foreign key {unq(t)}[{unq(c)}] should be hidden")
    if not any(t["date_table"] for t in tables.values()):
        issues.append("no table is marked as date table (dataCategory: Time)")
    model = (MODEL / "model.tmdl").read_text(encoding="utf-8")
    if any(t["calc_group"] for t in tables.values()) and "discourageImplicitMeasures" not in model:
        issues.append("calculation groups require discourageImplicitMeasures")
    for tname in tables:
        if f"ref table {tname}" not in model and f"ref table '{tname}'" not in model:
            issues.append(f"table {tname} is not referenced in model.tmdl")
    n_measures = len(all_measures)
    documented = sum(m["desc"] for t in tables.values() for m in t["measures"].values())
    print(f"{len(tables)} tables · {n_measures} measures · {documented} with descriptions")
    for i in issues:
        print("  ✗", i)
    print("model lint:", "FAILED" if issues else "passed")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
