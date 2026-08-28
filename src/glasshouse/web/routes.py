"""The Control Room routes: the six screens over the shared query
layers, including the browser's first governed writes (the Imports
workbench - fenced off in production until authenticated identity
lands).

Every handler is a thin composition: parse parameters, call the same
`glasshouse.api.queries` functions the JSON API serves (UI law 4), hand
the typed rows to a template. The org is an explicit query parameter on
every screen, mirroring the JSON API - no session, no cookie, no
auto-selection; a screen asked for without one goes to the picker. The
blotter's filter and pager and the audit screen's verify button are the
HTMX uses (they swap a results fragment in place); everything degrades
to ordinary GET navigation and plain form posts with JavaScript off.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import tempfile
from pathlib import Path
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse

from glasshouse.api import audit as audit_queries
from glasshouse.api import curves as curve_queries
from glasshouse.api import health, queries
from glasshouse.api.auth import authenticated_actor
from glasshouse.api.curves import (
    CurveIntegrityError,
    IncomparableCurvesError,
    UnknownCurveVersionError,
)
from glasshouse.api.deps import (
    ClientDep,
    EngineDep,
    ProjectorDep,
    StoreDep,
)
from glasshouse.api.schemas import CurveVersion
from glasshouse.commit import MorphologError
from glasshouse.config import get_settings
from glasshouse.imports import (
    ImportFormatError,
    import_curves,
    import_trades,
    preview_curves,
    preview_trades,
)
from glasshouse.logging import get_logger
from glasshouse.projections import catch_up
from glasshouse.verify import verify as run_verify
from glasshouse.web.templating import templates

log = get_logger("glasshouse.web")

router = APIRouter(include_in_schema=False)

PAGE_SIZE = 50


def wants_fragment(request: Request) -> bool:
    """Whether htmx will place the response inside an element of the
    page it is on, so the chrome must be left out. `HX-Request` alone
    is the wrong question: a history restore is an htmx request too,
    but it targets the body and needs the whole page back, and htmx 4
    holds no local cache, so every back navigation asks. Every response
    shaped by this answer carries `VARIES_BY_REQUEST_TYPE`: the same URL
    now has two representations, and an HTTP cache that does not know
    the header is entitled to hand a restore the fragment it stored for
    a panel swap, which is the very bug the discriminator removes."""
    return request.headers.get("HX-Request-Type") == "partial"


VARIES_BY_REQUEST_TYPE = {"Vary": "HX-Request-Type"}


def unavailable_page(request: Request) -> Response:
    """The HTML face of `ReadUnavailableError` and `MorphologError`: the
    app-level handlers render this for `/ui` paths. Database-free by
    construction. A fragment request gets a fragment: htmx swaps error
    responses in, so the verdict lands where the operator is looking
    rather than nesting a whole page inside a results panel."""
    template = "partials/unavailable.html" if wants_fragment(request) else "error.html"
    return templates.TemplateResponse(
        request, template, {}, status_code=503, headers=VARIES_BY_REQUEST_TYPE
    )


def _utc_instant(raw: str | None) -> dt.datetime | None:
    """A `datetime-local` value carries no offset; the filter fields are
    labelled UTC, so the instant is *defined* as UTC here and nothing
    naive passes this boundary (law 9). An aware value (a hand-crafted
    query string) is converted, not refused."""
    if not raw:
        return None
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _chrome(engine: sa.Engine, org: str, active: str) -> dict[str, Any]:
    # The selector always includes the requested org, so a directly
    # addressed organisation with no projected activity stays visible
    # (tenancy remains explicit even on an empty screen).
    return {
        "org": org,
        "org_options": sorted({*queries.list_orgs(engine), org}),
        "active": active,
    }


@router.get("/")
def root() -> Response:
    return RedirectResponse("/ui")


@router.get("/ui")
def home(
    request: Request,
    engine: EngineDep,
    client: ClientDep,
    projector: ProjectorDep,
    org: str | None = None,
) -> Response:
    if not org:
        # The organisation picker is this same route without an org.
        return templates.TemplateResponse(request, "orgs.html", {"orgs": queries.list_orgs(engine)})
    context = _chrome(engine, org, "overview") | {
        "summary": queries.overview(engine, org=org),
        # The projector's own progress, where it runs in this process: a
        # cursor advances only when the ledger moves, so cursor age alone
        # cannot tell an idle projector from a stuck one.
        "projector": projector.status.progress() if projector else None,
        "health": health.checks(get_settings(), engine, client, projector),
    }
    return templates.TemplateResponse(request, "overview.html", context)


@router.get("/ui/blotter")
def blotter(
    request: Request,
    engine: EngineDep,
    org: str | None = None,
    book: str | None = None,
    market: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    if not org:
        return RedirectResponse("/ui", status_code=303)
    book, market = book or None, market or None
    # Ask for one row beyond the page to learn whether a next page
    # exists; the 51st row is never rendered.
    rows = queries.list_trades(
        engine, org=org, book=book, market=market, limit=PAGE_SIZE + 1, offset=offset
    )
    context: dict[str, Any] = {
        "org": org,
        "book": book,
        "market": market,
        "trades": rows[:PAGE_SIZE],
        "has_more": len(rows) > PAGE_SIZE,
        "offset": offset,
        "prev_offset": max(0, offset - PAGE_SIZE),
        "next_offset": offset + PAGE_SIZE,
    }
    if wants_fragment(request):
        return templates.TemplateResponse(
            request, "partials/blotter_table.html", context, headers=VARIES_BY_REQUEST_TYPE
        )
    return templates.TemplateResponse(
        request,
        "blotter.html",
        _chrome(engine, org, "blotter") | context,
        headers=VARIES_BY_REQUEST_TYPE,
    )


def _chains(versions: list[CurveVersion]) -> list[list[str]]:
    # Each supersession chain rendered newest-first: start from every
    # head (a version nothing supersedes) that has a lineage, and walk
    # the supersedes pointers back.
    by_version = {v.version: v for v in versions}
    chains = []
    for head in versions:
        if head.superseded_by is not None or head.supersedes is None:
            continue
        chain = [head.version]
        cursor = head
        while cursor.supersedes is not None and cursor.supersedes in by_version:
            chain.append(cursor.supersedes)
            cursor = by_version[cursor.supersedes]
        chains.append(chain)
    return chains


@router.get("/ui/curves")
def curves(
    request: Request,
    engine: EngineDep,
    client: ClientDep,
    store: StoreDep,
    org: str | None = None,
    market: str | None = None,
    base: str | None = None,
    compare: str | None = None,
) -> Response:
    if not org:
        return RedirectResponse("/ui", status_code=303)
    # Chrome first: a dead database is one verdict (the projection 503)
    # before any binary work.
    context = _chrome(engine, org, "curves")
    markets = curve_queries.list_markets(client, org=org)
    market = market or (markets[0] if markets else None)
    versions = (
        curve_queries.list_curve_versions(client, store, org=org, market=market) if market else []
    )
    diff = None
    base, compare = base or None, compare or None
    if market and base and compare:
        try:
            diff = curve_queries.curve_diff(
                client, store, org=org, market=market, base=base, compare=compare
            )
        except UnknownCurveVersionError as unknown:
            context |= {"title": "Unknown curve version", "message": str(unknown)}
            return templates.TemplateResponse(request, "error.html", context, status_code=404)
        except IncomparableCurvesError as incomparable:
            context |= {"title": "Not comparable", "message": str(incomparable)}
            return templates.TemplateResponse(request, "error.html", context, status_code=422)
        except CurveIntegrityError as broken:
            context |= {"title": "Integrity break", "message": str(broken)}
            return templates.TemplateResponse(request, "error.html", context, status_code=409)
    context |= {
        "markets": markets,
        "market": market,
        "versions": versions,
        "chains": _chains(versions),
        "base": base,
        "compare": compare,
        "diff": diff,
    }
    return templates.TemplateResponse(request, "curves.html", context)


# The browser import's bounds: the upload cap keeps the preview-commit
# round trip and the parse bounded; the row cap keeps the per-row
# explain procession and the batch bounded; the batch timeout bounds the
# one long-running invocation the client-wide timeout deliberately
# exempts. Demo-scale honest; a real book arrives via the CLI, unbounded.
SIZE_CAP = 512 * 1024
MAX_IMPORT_ROWS = 2_000
BATCH_TIMEOUT_SECONDS = 120.0

IMPORT_KINDS = ("trades", "curves")


def _import_refusal(
    request: Request, org: str, status_code: int, title: str, message: str
) -> Response:
    # Database-free by construction: a malformed upload is refused on
    # its own evidence, whatever state the read model is in.
    context: dict[str, Any] = {
        "org": org,
        "active": "imports",
        "title": title,
        "message": message,
    }
    return templates.TemplateResponse(request, "error.html", context, status_code=status_code)


def _read_upload(file: UploadFile) -> str:
    raw = file.file.read(SIZE_CAP + 1)
    if len(raw) > SIZE_CAP:
        raise _UploadRefusedError(
            413, "File too large", "The upload cap is 512 KiB; import larger files with the CLI."
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as undecodable:
        raise _UploadRefusedError(
            422, "Not a text file", "The file is not UTF-8 encoded CSV."
        ) from undecodable


class _UploadRefusedError(Exception):
    def __init__(self, status_code: int, title: str, message: str) -> None:
        super().__init__(message)
        self.status_code, self.title, self.message = status_code, title, message


def _checked_import_form(kind: str, actor: str) -> str | None:
    if kind not in IMPORT_KINDS:
        return "The kind must be trades or curves."
    if not actor.strip():
        return "An actor is required: the ledger records who asserted every import."
    return None


def _write_fence(request: Request, org: str) -> Response | None:
    # The write-path fence matrix: production never accepts browser
    # writes (a demo login is not production identity); a HOSTED demo
    # with no login configured refuses too - a missing env var must not
    # leave a public deployment writable; dev stays open, and a demo
    # with the login configured is guarded by the gate itself.
    settings = get_settings()
    if settings.environment == "production":
        return _import_refusal(
            request,
            org,
            403,
            "Browser imports are fenced off in production",
            "Typed-actor identity is a readiness-L0 convenience; in production, "
            "import through the CLI until authenticated identity lands.",
        )
    if settings.environment == "demo" and settings.demo_password is None:
        return _import_refusal(
            request,
            org,
            403,
            "Browser imports need the demo login",
            "This hosted demo refuses browser writes while GLASSHOUSE_DEMO_PASSWORD "
            "is unset; configure the demo login, or import through the CLI.",
        )
    return None


def _row_count_refusal(request: Request, org: str, text: str) -> Response | None:
    if text.count("\n") > MAX_IMPORT_ROWS:
        return _import_refusal(
            request,
            org,
            413,
            "Too many rows for a browser import",
            f"The browser path is capped at {MAX_IMPORT_ROWS} rows; "
            "import larger files with the CLI.",
        )
    return None


@router.get("/ui/imports")
def imports_home(
    request: Request,
    engine: EngineDep,
    org: str | None = None,
) -> Response:
    if not org:
        return RedirectResponse("/ui", status_code=303)
    context = _chrome(engine, org, "imports") | {
        "authenticated_actor": authenticated_actor(request)
    }
    return templates.TemplateResponse(request, "imports.html", context)


@router.post("/ui/imports/preview")
def imports_preview(
    request: Request,
    org: Annotated[str, Form()],
    kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    engine: EngineDep,
    client: ClientDep,
    actor: Annotated[str, Form()] = "",
) -> Response:
    fence = _write_fence(request, org)
    if fence:
        return fence
    # When the demo login is active, BOTH halves of a write's identity
    # derive from authenticated context - the actor from the login, the
    # org from configuration - never from the request body (the gate is
    # the identity authority); the typed field remains only for ungated
    # dev, where org stays the explicit L0 parameter.
    if authenticated_actor(request):
        actor = authenticated_actor(request) or actor
        org = get_settings().demo_org
    problem = _checked_import_form(kind, actor)
    if problem:
        return _import_refusal(request, org, 422, "Check the form", problem)
    try:
        text = _read_upload(file)
    except _UploadRefusedError as refused:
        return _import_refusal(request, org, refused.status_code, refused.title, refused.message)
    oversized = _row_count_refusal(request, org, text)
    if oversized:
        return oversized
    try:
        preview = preview_trades if kind == "trades" else preview_curves
        report = preview(client, text, org=org, actor=actor)
    except ImportFormatError as refusal:
        return _import_refusal(request, org, 422, "The file was refused whole", str(refusal))
    context = _chrome(engine, org, "imports") | {
        "kind": kind,
        "actor": actor,
        "filename": file.filename,
        "report": report,
        # The commit step re-processes byte-for-byte what was previewed:
        # base64 survives the form round trip exactly (raw hidden fields
        # would suffer browser newline normalisation).
        "text_b64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }
    return templates.TemplateResponse(request, "import_preview.html", context)


@router.post("/ui/imports/commit")
def imports_commit(
    request: Request,
    org: Annotated[str, Form()],
    kind: Annotated[str, Form()],
    text_b64: Annotated[str, Form()],
    engine: EngineDep,
    client: ClientDep,
    store: StoreDep,
    actor: Annotated[str, Form()] = "",
) -> Response:
    fence = _write_fence(request, org)
    if fence:
        return fence
    if authenticated_actor(request):
        actor = authenticated_actor(request) or actor
        org = get_settings().demo_org
    problem = _checked_import_form(kind, actor)
    if problem:
        return _import_refusal(request, org, 422, "Check the form", problem)
    # The commit endpoint is directly reachable, so it enforces the same
    # cap as the upload - on the encoded length, before any decoding
    # (base64 inflates by 4/3, so this bound is exact for capped text).
    if len(text_b64) > ((SIZE_CAP + 2) // 3) * 4:
        return _import_refusal(
            request,
            org,
            413,
            "File too large",
            "The upload cap is 512 KiB; import larger files with the CLI.",
        )
    try:
        text = base64.b64decode(text_b64.encode("ascii"), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return _import_refusal(
            request,
            org,
            422,
            "The preview payload is damaged",
            "Upload and preview the file again.",
        )
    if len(text.encode("utf-8")) > SIZE_CAP:
        return _import_refusal(
            request,
            org,
            413,
            "File too large",
            "The upload cap is 512 KiB; import larger files with the CLI.",
        )
    oversized = _row_count_refusal(request, org, text)
    if oversized:
        return oversized
    try:
        if kind == "trades":
            report = import_trades(
                client, text, org=org, actor=actor, timeout=BATCH_TIMEOUT_SECONDS
            )
        else:
            report = import_curves(client, store, text, org=org, actor=actor)
    except ImportFormatError as refusal:
        return _import_refusal(request, org, 422, "The file was refused whole", str(refusal))
    except MorphologError as failure:
        # A WRITE failed operationally - never claim the ledger is
        # unaffected: a batch aborts between rows, so part of the file
        # may already be committed. Honest instructions, not reassurance.
        log.warning("web.import_failed", org=org, kind=kind, error=str(failure))
        return _import_refusal(
            request,
            org,
            500,
            "The import failed part-way",
            "The commit layer failed while processing this file, and some rows may "
            "already be committed. Nothing is lost: check the Audit screen for what "
            "landed, then re-run the same file - rows already committed come back "
            "as lawful rejections, never duplicates.",
        )
    # The inline projector mode: the screens read projections, so the
    # commit catches them up before showing receipts. A catch-up failure
    # must never cost the operator their receipts: the writes above are
    # committed, so render them with the lag stated instead of an error
    # page that would misreport the ledger. The chrome degrades for the
    # same reason - receipts outrank the org selector.
    applied: int | None
    try:
        applied = catch_up(client, engine)
    except (MorphologError, sa.exc.SQLAlchemyError) as lag:
        log.warning("web.import_projection_lagged", org=org, error=str(lag))
        applied = None
    try:
        chrome = _chrome(engine, org, "imports")
    except queries.ReadUnavailableError:
        chrome = {"org": org, "active": "imports"}
    context = chrome | {
        "kind": kind,
        "actor": actor,
        "report": report,
        "applied": applied,
    }
    return templates.TemplateResponse(request, "imports_result.html", context)


@router.get("/ui/audit")
def audit_screen(
    request: Request,
    engine: EngineDep,
    client: ClientDep,
    org: str | None = None,
    scope: str = "org",
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    if not org:
        return RedirectResponse("/ui", status_code=303)
    # The ledger read comes first (deliberately before the chrome): the
    # log is the screen's subject, and a binary that cannot answer is
    # its own honest 503 whatever the read model is doing. The default
    # view is SCOPED to the org (the tenancy boundary) and says so; the
    # whole-ledger view is an explicit step, labelled as the auditor
    # view (honesty-labelled at L0, authenticated once identity lands).
    entries = audit_queries.list_audit(client)
    ledger_total = len(entries)
    if scope != "ledger":
        scope = "org"
        entries = [e for e in entries if audit_queries.mentions_org(e, org)]
    page = entries[offset : offset + PAGE_SIZE]
    context = _chrome(engine, org, "audit") | {
        "rows": [(entry, audit_queries.mentions_org(entry, org)) for entry in page],
        "scope": scope,
        "total": len(entries),
        "ledger_total": ledger_total,
        "offset": offset,
        "prev_offset": max(0, offset - PAGE_SIZE),
        "next_offset": offset + PAGE_SIZE,
        "has_more": offset + PAGE_SIZE < len(entries),
        "report": None,
    }
    return templates.TemplateResponse(request, "audit.html", context)


@router.post("/ui/audit/verify")
def audit_verify(
    request: Request,
    org: Annotated[str, Form()],
    engine: EngineDep,
    client: ClientDep,
    store: StoreDep,
) -> Response:
    # Read-only however many times it is pressed; several bounded
    # subprocess calls, so it runs on demand, never on page load.
    report = run_verify(client, engine, store)
    if wants_fragment(request):
        return templates.TemplateResponse(
            request,
            "partials/verify_report.html",
            {"report": report},
            headers=VARIES_BY_REQUEST_TYPE,
        )
    entries = audit_queries.list_audit(client)
    ledger_total = len(entries)
    entries = [e for e in entries if audit_queries.mentions_org(e, org)]
    context = _chrome(engine, org, "audit") | {
        "rows": [(entry, True) for entry in entries[:PAGE_SIZE]],
        "scope": "org",
        "ledger_total": ledger_total,
        "total": len(entries),
        "offset": 0,
        "prev_offset": 0,
        "next_offset": PAGE_SIZE,
        "has_more": len(entries) > PAGE_SIZE,
        "report": report,
    }
    return templates.TemplateResponse(
        request, "audit.html", context, headers=VARIES_BY_REQUEST_TYPE
    )


@router.get("/ui/audit/evidence-pack")
def evidence_pack_download(
    client: ClientDep,
) -> Response:
    # The binary's exact pack bytes, straight to the operator's machine:
    # a pack is only evidence if it leaves the database's blast radius.
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "pack.json"
        client.export_evidence_pack(path)
        payload = path.read_bytes()
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="glasshouse-evidence-pack.json"'},
    )


@router.post("/ui/audit/checkpoint")
def checkpoint_download(
    client: ClientDep,
) -> Response:
    # A download deliberately, not server-side storage: an anchor only
    # anchors when held outside the database it checks. Re-pressing is
    # lawful (a no-new-rows checkpoint still yields valid anchor JSON).
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "anchor.json"
        client.write_checkpoint(path)
        payload = path.read_bytes()
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="glasshouse-checkpoint-anchor.json"'},
    )


@router.get("/ui/positions")
def positions(
    request: Request,
    engine: EngineDep,
    org: str | None = None,
    book: str | None = None,
    market: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> Response:
    if not org:
        return RedirectResponse("/ui", status_code=303)
    book, market = book or None, market or None
    try:
        start_at, end_at = _utc_instant(start), _utc_instant(end)
    except ValueError:
        # Database-free on purpose (no chrome query): a malformed filter
        # is a 422 whatever state the read model is in.
        context: dict[str, Any] = {
            "org": org,
            "active": "positions",
            "title": "Check the time window",
            "message": "The From and To filters must look like 2026-07-01T00:00 "
            "and are read as UTC instants.",
        }
        return templates.TemplateResponse(request, "error.html", context, status_code=422)
    context = _chrome(engine, org, "positions") | {
        "book": book,
        "market": market,
        "start": start or "",
        "end": end or "",
        "positions": queries.list_positions(
            engine, org=org, book=book, market=market, start=start_at, end=end_at
        ),
        # The one filter bar governs both sections: the marks narrow by
        # the same book and market as the positions above them.
        "valuations": queries.list_valuations(
            engine, org=org, book=book, market=market, latest=True
        ),
    }
    return templates.TemplateResponse(request, "positions.html", context)
