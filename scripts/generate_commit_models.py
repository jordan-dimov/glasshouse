#!/usr/bin/env python3
"""Generate typed commit models from a morpholog schema manifest.

The core (`generate`) is manifest-in, Python-source-out, and knows
nothing about Glasshouse: it maps every transformation in the manifest
to a request model, every predicate to a read model, and stamps the
output with the model hash. The skin at the bottom pins this
repository's paths and the two-leg drift story:

    uv run python scripts/generate_commit_models.py --refresh-manifest
        re-extract manifest.json from the .morph via the binary
        (needs morpholog; dev machines only), then regenerate
    uv run python scripts/generate_commit_models.py
        regenerate generated.py from the committed manifest (pure)
    uv run python scripts/generate_commit_models.py --check
        fail if generated.py does not match the committed manifest
        (pure; this is the CI leg - the .morph-to-manifest leg is the
        integration test asserting model_hash() == MODEL_HASH)

Loud by design: an unmapped kind, an unsupported schema fragment or a
field name that is not a plain Python identifier refuses to generate,
rather than generating something silently wrong.
"""

from __future__ import annotations

import argparse
import json
import keyword
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Predicate declarations carry kinds; transformation schemas carry JSON
# Schema fragments. Both map onto the same four Python types plus str.
KIND_TYPES = {
    "Subject": "str",
    "Decimal": "Decimal",
    "Date": "dt.date",
    "Timestamp": "AwareDatetime",
    "Bool": "bool",
}
TYPE_IMPORTS = {
    "Decimal": "from decimal import Decimal",
    "dt.date": "import datetime as dt",
    "AwareDatetime": "from pydantic import AwareDatetime",
}


def fragment_type(owner: str, name: str, fragment: dict[str, Any]) -> str:
    """The Python annotation for one transformation-argument schema
    fragment, refusing anything it does not positively recognise."""
    match fragment:
        case {"type": "boolean"}:
            return "bool"
        case {"type": "string", "format": "date"}:
            return "dt.date"
        case {"type": "string", "format": "date-time"}:
            return "AwareDatetime"
        case {"type": "string", "format": fmt}:
            raise ValueError(f"{owner}.{name}: unsupported format {fmt!r}")
        case {"type": "string", "pattern": _}:
            return "Decimal"  # the only patterned string the schema emits
        case {"type": "string"}:
            return "str"
        case _:
            raise ValueError(f"{owner}.{name}: unsupported schema fragment {fragment!r}")


def kind_type(owner: str, name: str, kind: str) -> str:
    if kind not in KIND_TYPES:
        raise ValueError(f"{owner}.{name}: unsupported kind {kind!r}")
    return KIND_TYPES[kind]


def pascal(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def emit_model(
    class_name: str,
    base: str,
    tag_field: str,
    tag_value: str,
    doc: str,
    fields: list[tuple[str, str]],
) -> list[str]:
    for name, _ in fields:
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError(f"{tag_value}.{name}: not a usable Python field name")
    lines = [
        "",
        "",
        f"class {class_name}({base}):",
        f'    """{doc}"""',
        "",
        f'    {tag_field}: ClassVar[str] = "{tag_value}"',
        "",
    ]
    lines += [f"    {name}: {annotation}" for name, annotation in fields]
    return lines


def generate(manifest: dict[str, Any], bases_module: str) -> str:
    """Render the whole generated module from one manifest."""
    bodies: list[str] = []
    used_types: set[str] = set()

    def note(annotation: str) -> str:
        used_types.add(annotation)
        return annotation

    bases_used: set[str] = set()
    for name, schema in manifest["transformations"].items():
        order = schema["x-morpholog-arg-order"]
        fields = [(arg, note(fragment_type(name, arg, schema["properties"][arg]))) for arg in order]
        bases_used.add("CommitRequest")
        bodies += emit_model(
            pascal(name),
            "CommitRequest",
            "TRANSFORMATION",
            name,
            f"Request for transformation `{name}`.",
            fields,
        )
    for decl in manifest["predicates"]:
        name = decl["name"]
        fields = [
            (arg["name"], note(kind_type(name, arg["name"], arg["kind"]))) for arg in decl["args"]
        ]
        bases_used.add("ClaimRow")
        bodies += emit_model(
            f"{name}Claim",
            "ClaimRow",
            "PREDICATE",
            name,
            f"Read model for predicate `{name}`.",
            fields,
        )

    imports = ["from __future__ import annotations", ""]
    if "dt.date" in used_types:
        imports += ["import datetime as dt"]
    if "Decimal" in used_types:
        imports += ["from decimal import Decimal"]
    imports += ["from typing import ClassVar", ""]
    if "AwareDatetime" in used_types:
        imports += ["from pydantic import AwareDatetime", ""]
    imports += [f"from {bases_module} import {', '.join(sorted(bases_used))}"]

    header = [
        '"""Typed commit models for morpholog programme '
        f"`{manifest['program']}`. GENERATED - DO NOT EDIT.",
        "",
        "Regenerate with scripts/generate_commit_models.py; the manifest",
        "is the single input and the model hash pins the rules in force.",
        '"""',
        "",
    ]
    footer = [
        "",
        "",
        f'MODEL_HASH = "{manifest["hash"]}"',
        f'PROGRAM = "{manifest["program"]}"',
        "",
    ]
    return "\n".join(header + imports + bodies + footer)


# The Glasshouse skin: this repository's paths.
PACKAGE = Path(__file__).resolve().parents[1] / "src" / "glasshouse" / "commit"
MODEL = PACKAGE / "glasshouse.morph"
MANIFEST = PACKAGE / "manifest.json"
OUTPUT = PACKAGE / "generated.py"
BASES_MODULE = "glasshouse.commit.bases"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-manifest", action="store_true")
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()

    if options.refresh_manifest:
        binary = os.environ.get("GLASSHOUSE_MORPHOLOG_BIN", "morpholog")
        proc = subprocess.run(
            [binary, "schema", str(MODEL), "--all"], capture_output=True, text=True
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            print(f"manifest extraction failed:\n{proc.stderr}", file=sys.stderr)
            return 1
        MANIFEST.write_text(proc.stdout)
        print(f"refreshed {MANIFEST}")

    source = generate(json.loads(MANIFEST.read_text()), BASES_MODULE)
    if options.check:
        if OUTPUT.read_text() != source:
            print(
                f"{OUTPUT} is stale: regenerate with "
                "`uv run python scripts/generate_commit_models.py`",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT} matches the committed manifest")
        return 0
    OUTPUT.write_text(source)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
