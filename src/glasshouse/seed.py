"""`glasshouse seed`: the Monday-morning demo dataset, deterministically.

One organisation (acme-energy), two books, six trades across two
counterparties with buys and sells, one official 24-hour curve, one
admitted mark per trade, projections rebuilt from zero - enough for
every screen to show a negative somewhere (a net-short delivery hour, a
negative mark) without fabricating anything. Business values are
deterministic constants; transition ids and commit instants are
intentionally fresh on every run.

Every write goes through the commit layer (law 1) - seeding is ordinary
governed traffic, not a fixture side door. The destructive `--reset`
path mirrors the integration tests' provisioning (drop the governed and
view schemas, drop the app tables, migrate to head) and is fenced twice:
by environment (never production; in dev only a local database) and by a
session advisory lock so a manual run and the nightly cron cannot
overlap. This module never prints; the CLI renders the report and turns
`SeedError` into stderr plus exit 1.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, apply_views, models
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import register_curve_version, value_trade
from glasshouse.compute.store import CurveStore, engine_url
from glasshouse.compute.store import metadata as payload_metadata
from glasshouse.config import Environment, get_settings
from glasshouse.projections import rebuild
from glasshouse.projections.tables import metadata as projection_metadata
from glasshouse.verify import verify

# One session-level advisory lock for the whole seed/reset operation.
SEED_LOCK_KEY = 423_001

ORG = "acme-energy"
MARKET = "de-power"
BOOKS = ("spec-de", "hedge-de")
CURVE_VERSION = "crv-2026-07-01"
AS_OF = dt.date(2026, 7, 1)
DAY = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

# The official curve: 24 hourly prices, 70 EUR/MWh at midnight rising by
# one each hour - dull on purpose, so every mark is checkable by eye.
CURVE = HourlyCurve(
    tuple((DAY + dt.timedelta(hours=hour), Decimal(str(70 + hour))) for hour in range(24))
)

# Six trades: both books, both directions, two counterparties, at least
# one net-short delivery hour (T-006 alone in hedge-de at 09-11) and at
# least one negative mark (T-001, struck well above the curve).
_TRADES: tuple[tuple[str, str, str, str, str, str, int, int], ...] = (
    # (trade, book, counterparty, direction, quantity, price, start hour, end hour)
    ("T-001", "spec-de", "stadtwerk-x", "buy", "10", "85", 8, 12),
    ("T-002", "spec-de", "nordkraft", "sell", "7.5", "82", 8, 10),
    ("T-003", "hedge-de", "nordkraft", "buy", "5", "70", 0, 6),
    ("T-004", "hedge-de", "stadtwerk-x", "sell", "12.5", "95", 18, 22),
    ("T-005", "spec-de", "stadtwerk-x", "buy", "20", "84", 12, 18),
    ("T-006", "hedge-de", "nordkraft", "sell", "15", "80", 9, 11),
)


class SeedError(Exception):
    """The seed cannot proceed honestly (wrong environment, a ledger
    that is not empty, an overlapping run, or a failed verification)."""


@dataclass(frozen=True)
class SeedReport:
    org: str
    books: int
    trades: int
    curves: int
    valuations: int

    def render(self) -> str:
        return (
            f"seeded {self.org}: {self.books} book(s), {self.trades} trade(s), "
            f"{self.curves} curve version(s), {self.valuations} valuation(s); "
            "verify: consistent"
        )


def _is_local(database_url: str) -> bool:
    host = sa.engine.url.make_url(engine_url(database_url)).host
    return host in (None, "", "localhost", "127.0.0.1", "::1")


def refuse_unsafe_reset(database_url: str, environment: Environment) -> None:
    """The destructive fence, applied before any connection exists:
    never production; in dev only a local database (the nightly demo
    cron runs with GLASSHOUSE_ENVIRONMENT=demo, its own explicit
    opt-in)."""
    if environment == "production":
        raise SeedError("seed --reset refuses to run in production")
    if environment == "dev" and not _is_local(database_url):
        raise SeedError(
            "seed --reset in a dev environment only runs against a local "
            "database; refusing a hosted one"
        )


def reset_app_state(engine: sa.Engine, database_url: str) -> None:
    """The integration tests' clean slate, in product form: drop the
    governed schema, the view surface and every app-schema table, then
    migrate the app schema to head. Needs `alembic.ini` at the repo (or
    Docker image) root - a wheel-only install cannot reset."""
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA IF EXISTS morpholog CASCADE"))
        connection.execute(sa.text("DROP SCHEMA IF EXISTS morpholog_views CASCADE"))
        payload_metadata.drop_all(connection)
        projection_metadata.drop_all(connection)
        connection.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    if not ini.exists():
        raise SeedError(
            f"alembic.ini not found at {ini}; seed --reset runs from the source "
            "checkout or the Docker image, not a wheel-only install"
        )
    config = Config(str(ini))
    config.set_main_option("sqlalchemy.url", engine_url(database_url))
    command.upgrade(config, "head")


def seed_demo(client: GlasshouseClient, store: CurveStore, engine: sa.Engine) -> SeedReport:
    """Provision, apply the inspection model, then run the Monday-morning
    story as governed traffic: grants, captures, the official curve, one
    mark per trade, projections rebuilt. Refuses a ledger with any
    history - idempotent by refusal, never additive by accident."""
    client.init(skip_if_exists=True)
    if client.audit():
        raise SeedError("the ledger already has transitions; use seed --reset")
    apply_views(engine)

    grants: tuple[object, ...] = (
        *(models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=b) for b in BOOKS),
        models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=MARKET),
        *(
            models.GrantValuationAuthorityRequest(principal="risk-engine", org=ORG, book=b)
            for b in BOOKS
        ),
    )
    for grant in grants:
        _committed(client.submit(grant, actor="bootstrap"))

    for trade, book, counterparty, direction, quantity, price, start, end in _TRADES:
        _committed(
            client.submit(
                models.CaptureTradeRequest(
                    org=ORG,
                    book=book,
                    trade=trade,
                    counterparty=counterparty,
                    market=MARKET,
                    direction=direction,
                    quantity=Decimal(quantity),
                    price=Decimal(price),
                    delivery_start=DAY + dt.timedelta(hours=start),
                    delivery_end=DAY + dt.timedelta(hours=end),
                ),
                actor="alice",
            )
        )

    _committed(
        register_curve_version(
            client,
            store,
            actor="carol",
            org=ORG,
            market=MARKET,
            as_of=AS_OF,
            version=CURVE_VERSION,
            curve=CURVE,
        )
    )
    for trade, book, *_ in _TRADES:
        _committed(value_trade(client, store, actor="risk-engine", org=ORG, book=book, trade=trade))

    rebuild(client, engine)
    return SeedReport(
        org=ORG, books=len(BOOKS), trades=len(_TRADES), curves=1, valuations=len(_TRADES)
    )


def _committed(outcome: object) -> None:
    if not isinstance(outcome, Committed):
        raise SeedError(f"a seed write was not committed: {outcome!r}")


def run_seed(database_url: str, *, reset: bool) -> SeedReport:
    """The whole operation: fence, lock, (reset,) seed, verify. The
    report only returns - and the CLI only prints it - after all six
    verification legs are green, so a nightly reset that seeded a
    divergent world exits loudly instead of quietly succeeding."""
    if reset:
        refuse_unsafe_reset(database_url, get_settings().environment)
    engine = sa.create_engine(engine_url(database_url))
    # The guard takes a session-level advisory lock on an AUTOCOMMIT
    # connection: a plain connection would sit "idle in transaction" for
    # the whole seed, and the blessed audit tail's slow-writer horizon
    # would (correctly) treat that open transaction as a writer that
    # started before every commit - making the projector see an empty
    # tail. Session locks outlive statements, so AUTOCOMMIT loses nothing.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as guard:
        locked = guard.execute(
            sa.text("select pg_try_advisory_lock(:key)"), {"key": SEED_LOCK_KEY}
        ).scalar()
        if not locked:
            raise SeedError("another seed or reset is already running; refusing to overlap")
        try:
            if reset:
                reset_app_state(engine, database_url)
            client = GlasshouseClient(str(MODEL_FILE), database_url)
            store = CurveStore(engine)
            report = seed_demo(client, store, engine)
            verification = verify(client, engine, store)
            if not verification.ok:
                raise SeedError(f"seeded, but verification failed:\n{verification.render()}")
            return report
        finally:
            guard.execute(sa.text("select pg_advisory_unlock(:key)"), {"key": SEED_LOCK_KEY})
