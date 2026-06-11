"""The operator's command line.

Imports today; the projector and the demo seed join at their
milestones. Argparse and boring on purpose: a file that does not match
its column contract refuses whole (exit 1); a file that was processed
exits 0 whatever the per-row outcomes, because partial admission is an
import's normal result - the report, not the exit code, is the answer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa

from glasshouse.commit import MODEL_FILE, GlasshouseClient, MorphologError
from glasshouse.compute.store import CurveStore, engine_url
from glasshouse.config import get_settings
from glasshouse.imports import ImportFormatError, import_curves, import_trades


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glasshouse", description="Glasshouse operational commands."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        (
            "import-trades",
            "Import a trades CSV (book,trade,counterparty,market,direction,quantity,"
            "price,delivery_start,delivery_end).",
        ),
        ("import-curves", "Import a curves CSV (market,as_of,version,period_start,price)."),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("file", type=Path, help="the CSV file")
        command.add_argument("--org", required=True, help="the organisation imported into")
        command.add_argument("--actor", required=True, help="the operator running the import")
        command.add_argument(
            "--database-url",
            default=get_settings().database_url,
            help="defaults to GLASSHOUSE_DATABASE_URL",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        text = args.file.read_text()
        client = GlasshouseClient(str(MODEL_FILE), args.database_url)
        if args.command == "import-trades":
            report = import_trades(client, text, org=args.org, actor=args.actor)
        else:
            engine = sa.create_engine(engine_url(args.database_url))
            report = import_curves(client, CurveStore(engine), text, org=args.org, actor=args.actor)
    except (ImportFormatError, MorphologError, OSError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1
    print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
