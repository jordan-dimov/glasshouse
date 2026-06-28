"""The operator's command line.

Imports, the projector and `verify` today; the demo seed joins at its
milestone.
Argparse and boring on purpose: a file that does not match its column
contract refuses whole (exit 1); a file that was processed exits 0
whatever the per-row outcomes, because partial admission is an import's
normal result - the report, not the exit code, is the answer.

The projector's three run modes meet the CLI here: `--project` on the
import commands is the inline mode (catch up after the write),
`glasshouse project` is the one-shot, and `glasshouse project --follow`
is the separate-worker loop.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa

from glasshouse.commit import (
    MODEL_FILE,
    VIEWS_SCHEMA,
    GlasshouseClient,
    MorphologError,
    apply_views,
)
from glasshouse.commit.morpholog_client.envelopes import CheckpointCreated, TreeIntact
from glasshouse.compute.store import CurveStore, engine_url
from glasshouse.config import get_settings
from glasshouse.imports import (
    ImportFormatError,
    import_curves,
    import_trades,
    preview_curves,
    preview_trades,
)
from glasshouse.logging import configure_logging, get_logger
from glasshouse.projections import catch_up, follow
from glasshouse.verify import verify

log = get_logger("glasshouse.cli")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glasshouse", description="Glasshouse operational commands."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def database_url(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--database-url",
            default=get_settings().database_url,
            help="defaults to GLASSHOUSE_DATABASE_URL",
        )

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
            "--project",
            action="store_true",
            help="catch the projections up after the import (the inline mode)",
        )
        command.add_argument(
            "--preview",
            action="store_true",
            help="dry-run: the ledger's admissibility verdict per row, nothing committed",
        )
        database_url(command)

    check = commands.add_parser(
        "verify", help="Prove the operational database still agrees with the governed ledger."
    )
    database_url(check)

    views = commands.add_parser(
        "apply-views",
        help="Apply the official inspection model: the generated per-predicate SQL views "
        "over governed state (run after `morpholog init`).",
    )
    database_url(views)

    project = commands.add_parser("project", help="Catch the projections up with the ledger.")
    project.add_argument(
        "--follow", action="store_true", help="keep polling (the separate-worker mode)"
    )
    project.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds")
    database_url(project)

    checkpoint = commands.add_parser(
        "checkpoint",
        help="Record a tamper-evidence checkpoint over the audit log's stable prefix "
        "(an external anchor the `verify` tree leg and evidence packs build on).",
    )
    database_url(checkpoint)

    pack = commands.add_parser(
        "evidence-export",
        help="Write a complete-prefix evidence pack to a file for offline verification "
        "(a third party verifies it with no database access).",
    )
    pack.add_argument("out", type=Path, help="the pack JSON file to write")
    database_url(pack)

    check_pack = commands.add_parser(
        "evidence-verify",
        help="Verify an evidence pack offline (no database): recompute the Merkle root and "
        "check it against the pack's checkpoints.",
    )
    check_pack.add_argument("pack", type=Path, help="the pack JSON file")
    check_pack.add_argument(
        "--anchor",
        type=Path,
        default=None,
        help="an externally-held checkpoint JSON the pack must be shown to extend",
    )
    # Verification is offline (no database is touched); the flag is here
    # only so client construction is uniform across commands.
    database_url(check_pack)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(get_settings())
    try:
        if args.command == "verify":
            client = GlasshouseClient(str(MODEL_FILE), args.database_url)
            engine = sa.create_engine(engine_url(args.database_url))
            verdict = verify(client, engine, CurveStore(engine))
            print(verdict.render())
            return 0 if verdict.ok else 1

        if args.command == "apply-views":
            engine = sa.create_engine(engine_url(args.database_url))
            apply_views(engine)
            print(f"applied the {VIEWS_SCHEMA} inspection model")
            return 0

        if args.command == "checkpoint":
            client = GlasshouseClient(str(MODEL_FILE), args.database_url)
            outcome = client.checkpoint()
            kind = "created" if isinstance(outcome, CheckpointCreated) else "no new rows"
            checkpoint = outcome.checkpoint
            print(
                f"checkpoint {kind}: tree_size {checkpoint.tree_size}, "
                f"hash {checkpoint.checkpoint_hash}"
            )
            return 0

        if args.command == "evidence-export":
            client = GlasshouseClient(str(MODEL_FILE), args.database_url)
            client.export_evidence_pack(args.out)
            print(f"evidence pack written to {args.out}")
            return 0

        if args.command == "evidence-verify":
            client = GlasshouseClient(str(MODEL_FILE), args.database_url)
            anchor = str(args.anchor) if args.anchor is not None else None
            pack_verdict = client.evidence_verify(str(args.pack), anchor_file=anchor)
            intact = isinstance(pack_verdict, TreeIntact)
            label = "intact" if intact else type(pack_verdict).__name__.removeprefix("Tree")
            print(f"evidence verify: {label}")
            return 0 if intact else 1

        if args.command == "project":
            client = GlasshouseClient(str(MODEL_FILE), args.database_url)
            engine = sa.create_engine(engine_url(args.database_url))
            if args.follow:
                follow(client, engine, interval_seconds=args.interval)
                return 0
            print(f"applied {catch_up(client, engine)} transition(s)")
            return 0

        text = args.file.read_text()
        client = GlasshouseClient(str(MODEL_FILE), args.database_url)
        if args.command == "import-trades":
            if args.preview:
                report = preview_trades(client, text, org=args.org, actor=args.actor)
            else:
                report = import_trades(client, text, org=args.org, actor=args.actor)
        elif args.preview:
            report = preview_curves(client, text, org=args.org, actor=args.actor)
        else:
            store = CurveStore(sa.create_engine(engine_url(args.database_url)))
            report = import_curves(client, store, text, org=args.org, actor=args.actor)
        print(report.render())
        if args.project:
            engine = sa.create_engine(engine_url(args.database_url))
            print(f"projected: applied {catch_up(client, engine)} transition(s)")
    except (ImportFormatError, MorphologError, OSError) as failure:
        # The whole-command refusal paths (a rejected header, an
        # unreadable file, a batch abort) get a structured event too, not
        # only the per-row import summaries that a successful run logs.
        log.warning(
            "cli.command_failed",
            command=args.command,
            error=type(failure).__name__,
            detail=str(failure),
        )
        print(f"error: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
