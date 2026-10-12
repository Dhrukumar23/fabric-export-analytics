"""Validates every PBIR JSON file against Microsoft's published schemas (offline copy).

    git clone --depth 1 https://github.com/microsoft/json-schemas.git /tmp/json-schemas
    python tools/validate_pbir.py /tmp/json-schemas
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

PREFIX = "https://developer.microsoft.com/json-schemas/"


def main(schema_root: str) -> int:
    root = Path(schema_root)
    cache: dict[str, Resource] = {}

    def retrieve(uri: str) -> Resource:
        if uri not in cache:
            path = root / uri.removeprefix(PREFIX)
            cache[uri] = Resource.from_contents(json.loads(path.read_text(encoding="utf-8")),
                                                default_specification=DRAFT7)
        return cache[uri]

    registry = Registry(retrieve=retrieve)
    files = sorted(Path(__file__).resolve().parents[1].glob("powerbi/*.Report/definition/**/*.json"))
    errors = 0
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        uri = doc.get("$schema")
        if not uri:
            continue
        # validate through a $ref so relative $refs inside the schema resolve against its own URI
        v = Draft7Validator({"$ref": uri}, registry=registry)
        errs = list(v.iter_errors(doc))
        for e in errs[:5]:
            print(f"{f.relative_to(f.parents[4])}: {'/'.join(map(str, e.absolute_path))}: {e.message[:200]}")
        errors += len(errs)
    print(f"validated {len(files)} files, {errors} schema errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/json-schemas"))
