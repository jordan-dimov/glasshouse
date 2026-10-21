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
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import sqlalchemy as sa
import typer
from pydantic import ValidationError
from typer.main import get_command

from glasshouse.commit import (
    MODEL_FILE,
    VIEWS_SCHEMA,
    GlasshouseClient,
    MorphologError,
    Rejected,
    apply_views,
)
from glasshouse.commit.morpholog_client.adapter import Request
from glasshouse.commit.morpholog_client.envelopes import (
    CheckpointCreated,
    RoleRebindingsEvaluated,
    TreeChainBroken,
    TreeIntact,
    TreeMalformedPack,
    TreeUnsupportedPack,
)
from glasshouse.commit.morpholog_client.models import (
    ChangePositionLimitRequest,
    PositionLimitClaim,
    SetPositionLimitRequest,
)
from glasshouse.compute.store import CurveStore, engine_url
from glasshouse.config import Settings, get_settings
from glasshouse.imports import (
    ImportFormatError,
    ImportIncompleteError,
    import_curves,
    import_trades,
    preview_curves,
    preview_trades,
    why,
)
from glasshouse.logging import configure_logging, get_logger
from glasshouse.projections import catch_up, follow
from glasshouse.provision import ProvisionError, run_provision
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


def _client(db: str) -> GlasshouseClient:
    """One construction site for the operator's client, so the writer-role
    assertion the audit tail needs on managed PostgreSQL is configuration,
    never something a new command can forget."""
    return GlasshouseClient(str(MODEL_FILE), db, writer_roles=get_settings().audit_writer_roles)


def _run_import(
    *, curves: bool, file: Path, org: str, actor: str, project: bool, preview: bool, db: str
) -> None:
    text = file.read_text(encoding="utf-8")
    client = _client(db)
    if not curves:
        try:
            report = (preview_trades if preview else import_trades)(
                client, text, org=org, actor=actor
            )
        except ImportIncompleteError as stopped:
            # The batch stopped: every row is still accounted for, so the
            # report is printed before the failure exits 1 - the rows that
            # may have committed are named in it, and only there.
            print(stopped.report.render())
            raise
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
    help="Import a trades CSV (book,trade,trade_date,counterparty,market,direction,quantity,"
    "price,delivery_start,delivery_end).",
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
    "provision",
    help="Non-destructive first-boot provisioning: migrate the app schema to head, initialise "
    "the governed schema if absent and migrate it, reconcile the managed indexes, apply the "
    "sealed inspection views. Idempotent - the web service's pre-deploy step.",
)
def provision_command(
    least_privilege: Annotated[
        bool,
        typer.Option(
            "--least-privilege",
            help="provision the governed schema with the reader/writer role floor (needs "
            "CREATEROLE; the report names the membership grants left to the operator)",
        ),
    ] = False,
    database_url: DatabaseUrl = "",
) -> None:
    settings = get_settings()
    report = run_provision(
        _db(database_url),
        least_privilege=least_privilege,
        writer_roles=settings.audit_writer_roles,
    )
    print(report.render())


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


@app.command(
    "set-limit",
    help="Set a book's per-hour position limit (the cap on its net position in any delivery "
    "hour), or change the standing one; enforced by the ledger at capture and amendment.",
)
def set_limit_command(
    org: Annotated[str, typer.Option(help="the organisation")],
    book: Annotated[str, typer.Option(help="the book the cap applies to")],
    cap: Annotated[str, typer.Option(help="the cap in MW, as written (never a float)")],
    actor: Annotated[str, typer.Option(help="the actor setting it (holds the limit authority)")],
    database_url: DatabaseUrl = "",
) -> None:
    client = _client(_db(database_url))
    cap_mw = Decimal(cap)
    standing = [limit for limit in client.read(PositionLimitClaim) if limit.book == book]
    request: Request = (
        ChangePositionLimitRequest(org=org, book=book, cap=cap_mw)
        if standing
        else SetPositionLimitRequest(org=org, book=book, cap=cap_mw)
    )
    outcome = client.submit(request, actor=actor, explain_on_reject=True)
    if isinstance(outcome, Rejected):
        print(f"refused: {why(outcome.explanation)}", file=sys.stderr)
        raise typer.Exit(code=1)
    was = f" (was {standing[0].cap} MW)" if standing else ""
    print(
        f"position limit for {book} in {org}: {cap_mw} MW{was}; transition {outcome.transition_id}"
    )


@app.command("verify", help="Prove the operational database still agrees with the governed ledger.")
def verify_command(database_url: DatabaseUrl = "") -> None:
    db = _db(database_url)
    client = _client(db)
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
    client = _client(db)
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
    covering: Annotated[
        str | None,
        typer.Option(
            help="a transition id the recorded head must contain (a close's last transition): "
            "refused, with nothing recorded, while it is outside the stable prefix"
        ),
    ] = None,
    database_url: DatabaseUrl = "",
) -> None:
    client = _client(_db(database_url))
    outcome = (
        client.write_checkpoint(out, covering=covering)
        if out
        else client.audit_checkpoint(covering=covering)
    )
    kind = "created" if isinstance(outcome, CheckpointCreated) else "no new rows"
    checkpoint = outcome.checkpoint
    covered = f", covering {covering}" if covering else ""
    print(
        f"checkpoint {kind}: tree_size {checkpoint.tree_size}, "
        f"hash {checkpoint.checkpoint_hash}{covered}"
    )
    if out:
        print(f"anchor written to {out}")


@app.command(
    "evidence-export",
    help="Write a complete-prefix evidence pack to a file for offline verification (a third "
    "party verifies it with no database access).",
)
def evidence_export_command(
    out: Annotated[Path, typer.Argument(help="the pack file to write (NDJSON)")],
    database_url: DatabaseUrl = "",
) -> None:
    client = _client(_db(database_url))
    manifest = client.audit_export(str(out))
    print(
        f"evidence pack written to {out}: {manifest.tree_size} row(s) under "
        f"{manifest.checkpoint_count} checkpoint(s)"
    )


@app.command(
    "evidence-verify",
    help="Verify an evidence pack offline (no database): recompute the Merkle root and check it "
    "against the pack's checkpoints.",
)
def evidence_verify_command(
    pack: Annotated[Path, typer.Argument(help="the pack file")],
    anchor: Annotated[
        Path | None,
        typer.Option(help="an externally-held checkpoint JSON the pack must be shown to extend"),
    ] = None,
) -> None:
    # Offline: no database is touched, so no --database-url - and no
    # settings either. This command is the forensic one, run by a third
    # party against a pack and an anchor; a malformed writer-role list or
    # a rejected demo password in the environment must not be able to
    # stop a valid pack from being verified.
    client = GlasshouseClient(str(MODEL_FILE), "")
    report = client.audit_verify_pack(str(pack), anchor_file=str(anchor) if anchor else None)
    verdict = report.verdict
    # The verdicts that carry the binary's own reason keep it: an old pack
    # (v0.0.15 reads one pack format) is told to be exported again, not
    # just called unsupported.
    match verdict:
        case TreeIntact():
            line = "intact"
        case TreeUnsupportedPack(detail=detail) | TreeMalformedPack(detail=detail):
            line = f"{type(verdict).__name__.removeprefix('Tree')}: {detail}"
        case TreeChainBroken(detail=detail):
            line = f"ChainBroken: {detail}"
        case _:
            line = type(verdict).__name__.removeprefix("Tree")
    print(f"evidence verify: {line}")
    # A login role seen under a new OID among the pack's rows: the role
    # was dropped and created again. A finding for the reader, never a
    # verdict - the rows still hash as they did.
    rebindings = report.role_rebindings
    if isinstance(rebindings, RoleRebindingsEvaluated):
        for change in rebindings.changes:
            print(
                f"role rebinding: {change.role} was oid {change.previous_oid} until "
                f"{change.last_observed_transition}, oid {change.new_oid} from "
                f"{change.first_observed_transition}"
            )
    if not isinstance(verdict, TreeIntact):
        raise typer.Exit(code=1)


def _logging_settings() -> Settings:
    """Configuring logging must never be the thing that refuses a command.

    A misconfigured environment (a weak demo password, a malformed
    writer-role list) is a real failure for any command that reads
    settings - and those raise below, cleanly. But `evidence-verify` is
    forensic: a third party runs it against a pack and an anchor, on a
    machine whose environment is none of their business, and it must not
    be blocked by a variable it never reads. So log like dev until a
    command proves otherwise."""
    try:
        return get_settings()
    except ValidationError:
        return Settings.model_construct()


def main(argv: list[str] | None = None) -> int:
    """Drive the Typer app and translate its outcome to an exit code.

    Typer runs in standalone mode (it renders usage errors and raises
    `SystemExit`); we catch that for the code, and catch the operational
    failures a command raises (a rejected header, an unreadable file, a
    binary or batch abort, a refused setting) to log one structured event
    and return 1.
    """
    configure_logging(_logging_settings())
    effective = sys.argv[1:] if argv is None else argv
    try:
        get_command(app).main(args=effective)
    except SystemExit as exit_:
        return int(exit_.code) if isinstance(exit_.code, int) else 0
    except (
        ImportFormatError,
        MorphologError,
        ProvisionError,
        SeedError,
        OSError,
        ValidationError,
    ) as failure:
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
