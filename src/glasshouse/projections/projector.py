"""The projector: the audit tail in, projection rows out, exactly once.

The transition log arrives through the blessed tail (`inspect audit
--named` via the generated client - the surface this projector forced
upstream as morpholog#136): committed transitions in `(committed_at,
transition_id)` order, resumed losslessly with `--after`, their claims
keyed by declared field rather than position. Lossless is
the binary's guarantee, not ours: `committed_at` is the writer's
transaction START instant while visibility follows commit order, so a
naive cursor over the raw table can skip a slow writer's transition
forever - the tail computes the resume horizon before snapshotting, and
rows it withholds surface on the next call.

Each fetched page's effects and the cursor advance happen in one
app-schema transaction under an advisory lock, so application is
exactly-once by construction and a second `catch_up` applies nothing.
`rebuild` deletes every projection row and replays from zero - the
read-side law as a callable, and the seed of `glasshouse verify`.

The fold is in two pure layers, so the projection logic is testable
without a database and the SQL applier and the in-memory replay cannot
drift apart. `fold_transition` classifies ONE transition's claims: a
capture (identity plus the first terms version), an amendment (a new
terms version with its lineage), a valuation - total over the model on
purpose, so the predicates it deliberately ignores are named and
anything else (a retraction of a projected predicate, terms that are
neither a capture's nor an amendment's, an unknown direction) raises
`ProjectionError`: the model changing under the folds should stop the
projector, never quietly corrupt the read side. `open_row`,
`advance_row` and `terms_delta` then turn a classified effect into row
effects given the trade's CURRENT row - current meaning the version
with the latest effective date in the log prefix, never the wall clock
and never log order - and both `_apply` (against the table, inside the
locked transaction) and `accumulate` (against a dict) call exactly
those functions on exactly that row shape.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from glasshouse.commit import GlasshouseClient, envelopes, models
from glasshouse.logging import get_logger
from glasshouse.projections.tables import (
    blotter_trade,
    position_hour,
    projection_progress,
    trade_terms_version,
    trade_valuation,
)

log = get_logger("glasshouse.projector")

HOUR = dt.timedelta(hours=1)
CURSOR = "needle"

# Predicates whose claims become projection rows. Retracting one of
# these is impossible under the current model (they are append only);
# seeing it happen means the model changed and the folds must too.
PROJECTED = frozenset({"TradeCaptured", "TradeTerms", "TradeTermsSupersedes", "TradeValued"})

# Predicates the needle's projections deliberately ignore: authority is
# queried from governed state, and curve officialness/lineage is read
# through the inspection surface, not materialised here (yet).
IGNORED = frozenset(
    {
        "MayCaptureTrade",
        "MayRegisterCurve",
        "MayValueTrade",
        "CurveRegistered",
        "OfficialCurve",
        "CurveSupersedes",
    }
)

SIGNS = {"buy": Decimal(1), "sell": Decimal(-1)}


class ProjectionError(RuntimeError):
    """The log carries something the folds do not honestly cover."""


@dataclass(frozen=True)
class PositionDelta:
    org: str
    book: str
    market: str
    period_start: dt.datetime
    delta_mw: Decimal


@dataclass(frozen=True)
class Capture:
    """A trade's identity with the first version of its terms."""

    identity: models.TradeCapturedClaim
    terms: models.TradeTermsClaim


@dataclass(frozen=True)
class Amendment:
    """A new version of a trade's terms, and the version it supersedes."""

    terms: models.TradeTermsClaim
    prior_version: str


@dataclass(frozen=True)
class Fold:
    """One transition's projection effects, classified."""

    captures: tuple[Capture, ...] = ()
    amendments: tuple[Amendment, ...] = ()
    valuations: tuple[models.TradeValuedClaim, ...] = ()


@dataclass(frozen=True)
class BlotterRow:
    """A `blotter_trade` row as data: the trade's identity, its current
    terms, and the provenance of both. `as_tuple` follows the table's
    column order, which is what `verify` compares."""

    org: str
    trade: str
    book: str
    counterparty: str
    market: str
    direction: str
    quantity: Decimal
    price: Decimal
    delivery_start: dt.datetime
    delivery_end: dt.datetime
    captured_at: dt.datetime
    transition_id: str
    actor: str
    trade_date: dt.date
    terms_version: str
    effective_from: dt.date
    amendment_count: int
    terms_at: dt.datetime
    terms_transition_id: str
    terms_actor: str

    @classmethod
    def from_mapping(cls, row: sa.RowMapping | Mapping[str, Any]) -> BlotterRow:
        return cls(**{column.name: row[column.name] for column in blotter_trade.c})

    def as_tuple(self) -> tuple[object, ...]:
        return tuple(getattr(self, column.name) for column in blotter_trade.c)

    def terms(self) -> models.TradeTermsClaim:
        """The current terms version, as the claim it came from."""
        return models.TradeTermsClaim(
            org=self.org,
            trade=self.trade,
            version=self.terms_version,
            quantity=self.quantity,
            price=self.price,
            delivery_start=self.delivery_start,
            delivery_end=self.delivery_end,
            effective_from=self.effective_from,
        )


def _hours(start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    hours = []
    cursor = start
    while cursor < end:
        hours.append(cursor)
        cursor += HOUR
    return hours


def fold_transition(
    asserted: list[envelopes.NamedClaim], retracted: list[envelopes.NamedClaim]
) -> Fold:
    """The pure classification: claims in, capture/amendment/valuation
    effects out, refusal on anything the folds do not cover.

    Claims arrive from the NAMED tail, keyed by declared field. The
    positional tail would decode the same values in declaration order,
    which is one silent reordering away from filing a counterparty under
    `market`: nothing on the positional audit path guards arity or order,
    where the named decode resolves fields under the programme's own
    authority and makes skew a hard error on the binary side. Law 4's
    "never read governed state via raw positional JSONB" applies to the
    log exactly as it does to the claims table."""
    for claim in retracted:
        if claim.predicate in PROJECTED:
            raise ProjectionError(
                f"the model retracted append-only {claim.predicate}; the folds must be revisited"
            )

    captured: dict[str, models.TradeCapturedClaim] = {}
    terms: list[models.TradeTermsClaim] = []
    supersedes: dict[str, str] = {}
    valuations: list[models.TradeValuedClaim] = []
    for claim in asserted:
        match claim.predicate:
            case "TradeCaptured":
                identity = models.TradeCapturedClaim.from_named(claim.args)
                captured[identity.trade] = identity
            case "TradeTerms":
                terms.append(models.TradeTermsClaim.from_named(claim.args))
            case "TradeTermsSupersedes":
                lineage = models.TradeTermsSupersedesClaim.from_named(claim.args)
                supersedes[lineage.new_version] = lineage.prior_version
            case "TradeValued":
                valuations.append(models.TradeValuedClaim.from_named(claim.args))
            case name if name in IGNORED:
                pass
            case name:
                raise ProjectionError(f"no fold covers asserted predicate {name!r}")

    for identity in captured.values():
        if identity.direction not in SIGNS:
            raise ProjectionError(f"no position sign for direction {identity.direction!r}")

    # A terms version is a capture's first version when its trade was
    # captured in this transition and no lineage names it; an amendment
    # when the lineage does. Anything else is a shape the model cannot
    # stage today, and a fold that guessed would corrupt the read side.
    captures: dict[str, Capture] = {}
    amendments: list[Amendment] = []
    for version in terms:
        if version.version in supersedes:
            amendments.append(Amendment(version, supersedes.pop(version.version)))
        elif version.trade in captured and version.trade not in captures:
            captures[version.trade] = Capture(captured[version.trade], version)
        else:
            raise ProjectionError(
                f"TradeTerms {version.version!r} of trade {version.trade!r} is neither a "
                "capture's first version nor an amendment; the folds must be revisited"
            )
    if supersedes:
        raise ProjectionError(
            f"TradeTermsSupersedes {', '.join(sorted(supersedes))} arrived without its TradeTerms"
        )
    if missing := sorted(set(captured) - set(captures)):
        raise ProjectionError(f"TradeCaptured {missing[0]!r} arrived without TradeTerms")
    dated = [(a.terms.trade, a.terms.effective_from) for a in amendments]
    if len(dated) != len(set(dated)):
        raise ProjectionError(
            "two versions of one trade's terms share an effective date in one transition; "
            "the ledger forbids this, so the folds must be revisited"
        )

    return Fold(
        tuple(captures.values()),
        tuple(sorted(amendments, key=lambda a: (a.terms.trade, a.terms.effective_from))),
        tuple(valuations),
    )


def terms_delta(
    identity: models.TradeCapturedClaim,
    prior: models.TradeTermsClaim | None,
    new: models.TradeTermsClaim,
) -> tuple[PositionDelta, ...]:
    """The signed MW change per delivery hour when `new` replaces `prior`
    (or opens the position, when `prior` is None): the new terms' hours
    added, the prior terms' hours taken away, only the hours that change.
    Pure, and the one place position arithmetic lives."""
    sign = SIGNS[identity.direction]
    by_hour: dict[dt.datetime, Decimal] = {}
    for hour in _hours(new.delivery_start, new.delivery_end):
        by_hour[hour] = by_hour.get(hour, Decimal(0)) + sign * new.quantity
    if prior is not None:
        for hour in _hours(prior.delivery_start, prior.delivery_end):
            by_hour[hour] = by_hour.get(hour, Decimal(0)) - sign * prior.quantity
    return tuple(
        PositionDelta(identity.org, identity.book, identity.market, hour, delta)
        for hour, delta in sorted(by_hour.items())
        if delta != 0
    )


def open_row(
    capture: Capture, at: dt.datetime, tid: str, actor: str
) -> tuple[BlotterRow, tuple[PositionDelta, ...]]:
    """A capture opens the trade's row on its first terms version."""
    identity, terms = capture.identity, capture.terms
    row = BlotterRow(
        org=identity.org,
        trade=identity.trade,
        book=identity.book,
        counterparty=identity.counterparty,
        market=identity.market,
        direction=identity.direction,
        quantity=terms.quantity,
        price=terms.price,
        delivery_start=terms.delivery_start,
        delivery_end=terms.delivery_end,
        captured_at=at,
        transition_id=tid,
        actor=actor,
        trade_date=terms.effective_from,
        terms_version=terms.version,
        effective_from=terms.effective_from,
        amendment_count=0,
        terms_at=at,
        terms_transition_id=tid,
        terms_actor=actor,
    )
    return row, terms_delta(identity, None, terms)


def advance_row(
    row: BlotterRow, amendment: Amendment, at: dt.datetime, tid: str, actor: str
) -> tuple[BlotterRow, tuple[PositionDelta, ...]]:
    """An amendment moves the row to the new version when that version's
    effective date is the latest the trade has seen; an earlier-dated
    version is counted but changes neither the current terms nor the
    position (unreachable under today's gate, which admits only strictly
    later dates - kept so the semantics are right if that is relaxed);
    an equal date is a state the ledger forbids."""
    new = amendment.terms
    if new.effective_from == row.effective_from:
        raise ProjectionError(
            f"terms {new.version!r} of trade {row.trade!r} share the effective date "
            f"{row.effective_from} with {row.terms_version!r}; the ledger forbids this"
        )
    if new.effective_from < row.effective_from:
        return replace(row, amendment_count=row.amendment_count + 1), ()
    identity = models.TradeCapturedClaim(
        org=row.org,
        book=row.book,
        trade=row.trade,
        counterparty=row.counterparty,
        market=row.market,
        direction=row.direction,
    )
    advanced = replace(
        row,
        quantity=new.quantity,
        price=new.price,
        delivery_start=new.delivery_start,
        delivery_end=new.delivery_end,
        terms_version=new.version,
        effective_from=new.effective_from,
        amendment_count=row.amendment_count + 1,
        terms_at=at,
        terms_transition_id=tid,
        terms_actor=actor,
    )
    return advanced, terms_delta(identity, row.terms(), new)


def history_row(
    terms: models.TradeTermsClaim,
    prior_version: str | None,
    at: dt.datetime,
    tid: str,
    actor: str,
) -> tuple[object, ...]:
    """A `trade_terms_version` row in the table's column order."""
    return (
        terms.org,
        terms.trade,
        terms.version,
        prior_version,
        terms.quantity,
        terms.price,
        terms.delivery_start,
        terms.delivery_end,
        terms.effective_from,
        at,
        tid,
        actor,
    )


def _merged(deltas: Iterable[PositionDelta]) -> list[PositionDelta]:
    """One delta per position-hour key: a transaction that touches the
    same hour twice (a capture and an amendment of one trade in one
    `transact`) must reach the upsert as one row."""
    by_key: dict[tuple[str, str, str, dt.datetime], Decimal] = {}
    for delta in deltas:
        key = (delta.org, delta.book, delta.market, delta.period_start)
        by_key[key] = by_key.get(key, Decimal(0)) + delta.delta_mw
    return [PositionDelta(*key, mw) for key, mw in sorted(by_key.items()) if mw != 0]


def _apply(
    connection: sa.Connection, fold: Fold, committed_at: dt.datetime, tid: str, actor: str
) -> None:
    deltas: list[PositionDelta] = []
    for capture in fold.captures:
        row, opened = open_row(capture, committed_at, tid, actor)
        connection.execute(
            sa.insert(blotter_trade).values(**_values(blotter_trade, row.as_tuple()))
        )
        connection.execute(
            sa.insert(trade_terms_version).values(
                **_values(
                    trade_terms_version, history_row(capture.terms, None, committed_at, tid, actor)
                )
            )
        )
        deltas.extend(opened)
    for amendment in fold.amendments:
        terms = amendment.terms
        # The trade's own row, read inside the locked transaction: a
        # deterministic function of the log prefix, exactly like the
        # running net the position upsert adds to.
        current = (
            connection.execute(
                sa.select(blotter_trade)
                .where(blotter_trade.c.org == terms.org, blotter_trade.c.trade == terms.trade)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if current is None:
            raise ProjectionError(
                f"an amendment of trade {terms.trade!r} arrived before its capture; "
                "the ledger forbids this, so the projection is not of this ledger"
            )
        advanced, moved = advance_row(
            BlotterRow.from_mapping(current), amendment, committed_at, tid, actor
        )
        connection.execute(
            sa.update(blotter_trade)
            .where(blotter_trade.c.org == terms.org, blotter_trade.c.trade == terms.trade)
            .values(**_values(blotter_trade, advanced.as_tuple()))
        )
        connection.execute(
            sa.insert(trade_terms_version).values(
                **_values(
                    trade_terms_version,
                    history_row(terms, amendment.prior_version, committed_at, tid, actor),
                )
            )
        )
        deltas.extend(moved)
    if merged := _merged(deltas):
        statement = pg_insert(position_hour).values(
            [
                {
                    "org": delta.org,
                    "book": delta.book,
                    "market": delta.market,
                    "period_start": delta.period_start,
                    "net_mw": delta.delta_mw,
                    "transition_id": tid,
                }
                for delta in merged
            ]
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["org", "book", "market", "period_start"],
                set_={
                    "net_mw": position_hour.c.net_mw + statement.excluded.net_mw,
                    "transition_id": statement.excluded.transition_id,
                },
            )
        )
    for valuation in fold.valuations:
        connection.execute(
            sa.insert(trade_valuation).values(
                org=valuation.org,
                trade=valuation.trade,
                curve_version=valuation.curve_version,
                book=valuation.book,
                mtm=valuation.mtm,
                valued_at=committed_at,
                transition_id=tid,
                actor=actor,
                terms_version=valuation.terms_version,
            )
        )


def _values(table: sa.Table, row: tuple[object, ...]) -> dict[str, object]:
    return dict(zip((column.name for column in table.c), row, strict=True))


def catch_up(client: GlasshouseClient, engine: sa.Engine) -> int:
    """Apply every transition after the cursor, one app-schema
    transaction per fetched page. Returns the number of transitions
    applied. Safe to run concurrently: the advisory lock serialises
    projectors, and the page is fetched under the authoritative cursor
    inside the locked transaction."""
    applied = 0
    while True:
        with engine.begin() as connection:
            # One writer per page: the transaction-scoped advisory lock
            # serialises concurrent projectors, and reading the cursor
            # after acquiring it (then fetching against that cursor)
            # makes double-application impossible rather than unlikely.
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext('glasshouse.projector'))")
            )
            cursor = connection.execute(
                sa.select(projection_progress.c.transition_id).where(
                    projection_progress.c.name == CURSOR
                )
            ).scalar_one_or_none()
            page = client.audit_named(after=cursor)
            if not page:
                return applied
            for row in page:
                fold = fold_transition(row.asserted_claims, row.retracted_claims)
                _apply(connection, fold, row.committed_at, row.transition_id, row.actor)
            last = page[-1]
            advance = pg_insert(projection_progress).values(
                name=CURSOR, committed_at=last.committed_at, transition_id=last.transition_id
            )
            connection.execute(
                advance.on_conflict_do_update(
                    index_elements=["name"],
                    set_={
                        "committed_at": last.committed_at,
                        "transition_id": last.transition_id,
                    },
                )
            )
        # Logged only after the transaction has committed: the exactly-once
        # application made observable, one event per locked page. An event
        # logged inside the transaction would lie if the commit rolled back.
        log.info(
            "projector.page_applied",
            transitions=len(page),
            resumed_from=cursor,
            cursor=last.transition_id,
        )
        applied += len(page)


def accumulate(
    client: GlasshouseClient, up_to: str | None = None
) -> dict[str, set[tuple[object, ...]]]:
    """Replay the tail through the pure folds into in-memory row sets
    matching the projection tables' column order - the non-destructive
    half of `glasshouse verify`'s projection leg. Reads the blessed
    tail and writes nothing. Returns one entry per projection table.

    With `up_to` (a transition id), folding stops after that
    transition: the caller is verifying tables that claim to reflect
    the log exactly up to their cursor, and anything beyond it is the
    projector's lag, not divergence. A `up_to` the tail does not
    contain raises `ProjectionError` - a cursor naming an unknown
    transition is corruption, never lag (committed-row visibility is
    monotonic, so a previously applied transition cannot vanish from a
    later snapshot)."""
    blotter: dict[tuple[str, str], BlotterRow] = {}
    history: dict[tuple[str, str, str], tuple[object, ...]] = {}
    positions: dict[tuple[str, str, str, dt.datetime], tuple[Decimal, str]] = {}
    valuations: dict[tuple[str, str, str, str], tuple[object, ...]] = {}
    cursor: tuple[object, ...] | None = None
    reached_up_to = up_to is None
    for row in client.audit_named():
        if up_to is not None and reached_up_to:
            break
        fold = fold_transition(row.asserted_claims, row.retracted_claims)
        at, tid, actor = row.committed_at, row.transition_id, row.actor
        deltas: list[PositionDelta] = []
        for capture in fold.captures:
            opened, moved = open_row(capture, at, tid, actor)
            blotter[opened.org, opened.trade] = opened
            history[opened.org, opened.trade, opened.terms_version] = history_row(
                capture.terms, None, at, tid, actor
            )
            deltas.extend(moved)
        for amendment in fold.amendments:
            terms = amendment.terms
            current = blotter.get((terms.org, terms.trade))
            if current is None:
                raise ProjectionError(
                    f"an amendment of trade {terms.trade!r} arrived before its capture; "
                    "the ledger forbids this, so the tail is not one ledger's"
                )
            advanced, moved = advance_row(current, amendment, at, tid, actor)
            blotter[terms.org, terms.trade] = advanced
            history[terms.org, terms.trade, terms.version] = history_row(
                terms, amendment.prior_version, at, tid, actor
            )
            deltas.extend(moved)
        for delta in _merged(deltas):
            key = (delta.org, delta.book, delta.market, delta.period_start)
            net = positions[key][0] if key in positions else Decimal(0)
            positions[key] = (net + delta.delta_mw, tid)
        for valuation in fold.valuations:
            valuations[
                valuation.org, valuation.trade, valuation.curve_version, valuation.terms_version
            ] = (
                valuation.org,
                valuation.trade,
                valuation.curve_version,
                valuation.book,
                valuation.mtm,
                at,
                tid,
                actor,
                valuation.terms_version,
            )
        cursor = (CURSOR, at, tid)
        if tid == up_to:
            reached_up_to = True
    if not reached_up_to:
        raise ProjectionError(
            f"the projection cursor names transition {up_to!r}, which the audit tail "
            "does not contain - the cursor does not describe this ledger"
        )
    return {
        "blotter_trade": {row.as_tuple() for row in blotter.values()},
        "trade_terms_version": set(history.values()),
        "position_hour": {(*key, net, tid) for key, (net, tid) in positions.items()},
        "trade_valuation": set(valuations.values()),
        "projection_progress": {cursor} if cursor else set(),
    }


def rebuild(client: GlasshouseClient, engine: sa.Engine) -> int:
    """The read-side law as a callable: delete every projection row and
    replay the tail from zero. Returns the number of transitions applied.

    Wipe, replay and cursor advance happen in ONE transaction holding
    the same advisory lock as a `catch_up` page, so a concurrent live
    projector can never interleave with a half-rebuilt world (an
    unlocked wipe once let a live page advance the cursor to the tip
    over freshly emptied tables - permanent divergence), and the replay
    is deterministic for a verify that runs immediately after (a
    two-step wipe-then-catch-up could be blinded by a lock-waiting
    transaction pinning the audit tail's resume horizon). The tail is
    fetched inside the open transaction: every transition to replay
    committed before this transaction began, so the horizon never
    withholds them."""
    log.warning("projector.rebuild_started")
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext('glasshouse.projector'))")
        )
        for table in (
            blotter_trade,
            trade_terms_version,
            position_hour,
            trade_valuation,
            projection_progress,
        ):
            connection.execute(sa.delete(table))
        rows = client.audit_named()
        for row in rows:
            fold = fold_transition(row.asserted_claims, row.retracted_claims)
            _apply(connection, fold, row.committed_at, row.transition_id, row.actor)
        if rows:
            last = rows[-1]
            connection.execute(
                pg_insert(projection_progress).values(
                    name=CURSOR, committed_at=last.committed_at, transition_id=last.transition_id
                )
            )
    log.warning("projector.rebuild_complete", transitions=len(rows))
    return len(rows)
