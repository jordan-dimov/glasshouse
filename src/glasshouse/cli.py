"""The operator's command line (Typer).

A thin `main(argv) -> int` wraps the Typer app so the exit-code
conventions hold and the tests can drive it: a file that does not match
its column contract refuses whole (exit 1); a file that was processed
exits 0 whatever the per-row outcomes, because partial admission is an
import's normal result (the report, not the exit code, is the answer);
`verify` and `evidence-verify` carry their verdict as the exit code.
stdout stays the data channel (reports, counts, verdicts); operational
logs go to stderr.

The projector's three run modes meet the CLI here: `--project` on the
import commands is the inline mode (catch up after the write),
`glasshouse project` is the one-shot, and `glasshouse project --follow`
is the separate-worker loop.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import sqlalchemy as sa
import typer
from typer.main import get_command

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
from glasshouse.seed import SeedError, run_seed
from glasshouse.verify import verify

log = get_logger("glasshouse.cli")

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Glasshouse operational commands.",
)

# `--database-url` defaults to GLASSHOUSE_DATABASE_URL, resolved at call
# time (not import time) so the environment is read when the command runs.
DatabaseUrl = Annotated[str, typer.Option(help="defaults to GLASSHOUSE_DATABASE_URL")]


def _db(database_url: str) -> str:
    return database_url or get_settings().database_url


def _run_import(
    *, curves: bool, file: Path, org: str, actor: str, project: bool, preview: bool, db: str
) -> None:
    text = file.read_text()
    client = GlasshouseClient(str(MODEL_FILE), db)
    if not curves:
        report = (preview_trades if preview else import_trades)(client, text, org=org, actor=actor)
    elif preview:
        report = preview_curves(client, text, org=org, actor=actor)
    else:
        store = CurveStore(sa.create_engine(engine_url(db)))
        report = import_curves(client, store, text, org=org, actor=actor)
    print(report.render())
    if project:
        engine = sa.create_engine(engine_url(db))
        print(f"projected: applied {catch_up(client, engine)} transition(s)")


@app.command(
    "import-trades",
    help="Import a trades CSV (book,trade,counterparty,market,direction,quantity,price,"
    "delivery_start,delivery_end).",
)
def import_trades_command(
    file: Annotated[Path, typer.Argument(help="the CSV file")],
    org: Annotated[str, typer.Option(help="the organisation imported into")],
    actor: Annotated[str, typer.Option(help="the operator running the import")],
    project: Annotated[
        bool, typer.Option(help="catch the projections up after the import (the inline mode)")
    ] = False,
    preview: Annotated[
        bool,
        typer.Option(help="dry-run: the ledger's admissibility verdict per row, nothing committed"),
    ] = False,
    database_url: DatabaseUrl = "",
) -> None:
    _run_import(
        curves=False,
        file=file,
        org=org,
        actor=actor,
        project=project,
        preview=preview,
        db=_db(database_url),
    )


@app.command("import-curves", help="Import a curves CSV (market,as_of,version,period_start,price).")
def import_curves_command(
    file: Annotated[Path, typer.Argument(help="the CSV file")],
    org: Annotated[str, typer.Option(help="the organisation imported into")],
    actor: Annotated[str, typer.Option(help="the operator running the import")],
    project: Annotated[
        bool, typer.Option(help="catch the projections up after the import (the inline mode)")
    ] = False,
    preview: Annotated[
        bool,
        typer.Option(help="dry-run: the ledger's admissibility verdict per row, nothing committed"),
    ] = False,
    database_url: DatabaseUrl = "",
) -> None:
    _run_import(
        curves=True,
        file=file,
        org=org,
        actor=actor,
        project=project,
        preview=preview,
        db=_db(database_url),
    )


@app.command(
    "seed",
    help="Seed the Monday-morning demo dataset (org acme-energy): grants, six trades, an "
    "official curve, valuations, projections - then verify all six legs before reporting.",
)
def seed_command(
    reset: Annotated[
        bool,
        typer.Option(
            "--reset",
            help="drop and re-provision the database first (destructive; refused in "
            "production, and in dev refused for non-local databases)",
        ),
    ] = False,
    database_url: DatabaseUrl = "",
) -> None:
    print(run_seed(_db(database_url), reset=reset).render())


@app.command("verify", help="Prove the operational database still agrees with the governed ledger.")
def verify_command(database_url: DatabaseUrl = "") -> None:
    db = _db(database_url)
    client = GlasshouseClient(str(MODEL_FILE), db)
    engine = sa.create_engine(engine_url(db))
    report = verify(client, engine, CurveStore(engine))
    print(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


@app.command(
    "apply-views",
    help="Apply the official inspection model: the generated per-predicate SQL views over "
    "governed state (run after `morpholog init`).",
)
def apply_views_command(database_url: DatabaseUrl = "") -> None:
    apply_views(sa.create_engine(engine_url(_db(database_url))))
    print(f"applied the {VIEWS_SCHEMA} inspection model")


@app.command("project", help="Catch the projections up with the ledger.")
def project_command(
    follow_: Annotated[
        bool, typer.Option("--follow", help="keep polling (the separate-worker mode)")
    ] = False,
    interval: Annotated[float, typer.Option(help="poll interval in seconds")] = 1.0,
    database_url: DatabaseUrl = "",
) -> None:
    db = _db(database_url)
    client = GlasshouseClient(str(MODEL_FILE), db)
    engine = sa.create_engine(engine_url(db))
    if follow_:
        follow(client, engine, interval_seconds=interval)
        return
    print(f"applied {catch_up(client, engine)} transition(s)")


@app.command(
    "checkpoint",
    help="Record a tamper-evidence checkpoint over the audit log's stable prefix (an external "
    "anchor the `verify` tree leg and evidence packs build on).",
)
def checkpoint_command(
    out: Annotated[
        Path | None,
        typer.Option(
            help="write the checkpoint JSON to this file - the external anchor that "
            "`evidence-verify --anchor` checks a pack against"
        ),
    ] = None,
    database_url: DatabaseUrl = "",
) -> None:
    client = GlasshouseClient(str(MODEL_FILE), _db(database_url))
    outcome = client.write_checkpoint(out) if out else client.checkpoint()
    kind = "created" if isinstance(outcome, CheckpointCreated) else "no new rows"
    checkpoint = outcome.checkpoint
    print(f"checkpoint {kind}: tree_size {checkpoint.tree_size}, hash {checkpoint.checkpoint_hash}")
    if out:
        print(f"anchor written to {out}")


@app.command(
    "evidence-export",
    help="Write a complete-prefix evidence pack to a file for offline verification (a third "
    "party verifies it with no database access).",
)
def evidence_export_command(
    out: Annotated[Path, typer.Argument(help="the pack JSON file to write")],
    database_url: DatabaseUrl = "",
) -> None:
    client = GlasshouseClient(str(MODEL_FILE), _db(database_url))
    client.export_evidence_pack(out)
    print(f"evidence pack written to {out}")


@app.command(
    "evidence-verify",
    help="Verify an evidence pack offline (no database): recompute the Merkle root and check it "
    "against the pack's checkpoints.",
)
def evidence_verify_command(
    pack: Annotated[Path, typer.Argument(help="the pack JSON file")],
    anchor: Annotated[
        Path | None,
        typer.Option(help="an externally-held checkpoint JSON the pack must be shown to extend"),
    ] = None,
) -> None:
    # Offline: no database is touched, so no --database-url.
    client = GlasshouseClient(str(MODEL_FILE), "")
    verdict = client.evidence_verify(str(pack), anchor_file=str(anchor) if anchor else None)
    intact = isinstance(verdict, TreeIntact)
    print(f"evidence verify: {'intact' if intact else type(verdict).__name__.removeprefix('Tree')}")
    if not intact:
        raise typer.Exit(code=1)


def main(argv: list[str] | None = None) -> int:
    """Drive the Typer app and translate its outcome to an exit code.

    Typer runs in standalone mode (it renders usage errors and raises
    `SystemExit`); we catch that for the code, and catch the operational
    failures a command raises (a rejected header, an unreadable file, a
    binary or batch abort) to log one structured event and return 1.
    """
    configure_logging(get_settings())
    effective = sys.argv[1:] if argv is None else argv
    try:
        get_command(app).main(args=effective)
    except SystemExit as exit_:
        return int(exit_.code) if isinstance(exit_.code, int) else 0
    except (ImportFormatError, MorphologError, SeedError, OSError) as failure:
        log.warning(
            "cli.command_failed",
            command=effective[0] if effective else "?",
            error=type(failure).__name__,
            detail=str(failure),
        )
        print(f"error: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
