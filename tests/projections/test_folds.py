"""The pure fold: claims in, classified effects out, refusal on anything
the folds do not honestly cover; and the row functions the SQL applier
and the in-memory replay share. No database anywhere in this module."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glasshouse.commit import envelopes, models
from glasshouse.compute.terms import current_terms
from glasshouse.projections import ProjectionError, fold_transition
from glasshouse.projections.projector import (
    Amendment,
    BlotterRow,
    Capture,
    advance_row,
    open_row,
    terms_delta,
)

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
D0 = dt.date(2026, 6, 30)

SIGN = {"buy": Decimal(1), "sell": Decimal(-1)}


def _ts(moment: dt.datetime) -> str:
    """As the named tail spells an instant: RFC 3339, zone-less UTC."""
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# The named tail carries BARE wire values - decimals, dates and instants
# as strings, decoded by the generated `from_named`. These fixtures spell
# them the way the binary does, so a fold that only works on
# already-typed values cannot pass here.
def captured(trade: str = "T-1", direction: str = "buy") -> envelopes.NamedClaim:
    return envelopes.NamedClaim(
        "TradeCaptured",
        {
            "org": "acme",
            "book": "spec-de",
            "trade": trade,
            "counterparty": "stadtwerk-x",
            "market": "de-power",
            "direction": direction,
        },
    )


def terms(
    trade: str = "T-1",
    quantity: str = "10",
    hours: int = 3,
    version: str | None = None,
    effective_from: dt.date = D0,
    start: dt.datetime = T0,
) -> envelopes.NamedClaim:
    return envelopes.NamedClaim(
        "TradeTerms",
        {
            "org": "acme",
            "trade": trade,
            "version": version or f"{trade}/v1",
            "quantity": quantity,
            "price": "86.25",
            "delivery_start": _ts(start),
            "delivery_end": _ts(start + dt.timedelta(hours=hours)),
            "effective_from": effective_from.isoformat(),
        },
    )


def supersedes(new_version: str, prior_version: str) -> envelopes.NamedClaim:
    return envelopes.NamedClaim(
        "TradeTermsSupersedes", {"new_version": new_version, "prior_version": prior_version}
    )


def _opened(fold_claims: list[envelopes.NamedClaim]) -> tuple[BlotterRow, tuple]:  # type: ignore[type-arg]
    (capture,) = fold_transition(fold_claims, []).captures
    return open_row(capture, T0, "tid-1", "alice")


def test_a_capture_becomes_one_blotter_row_and_hourly_deltas() -> None:
    fold = fold_transition([captured(), terms()], [])
    (capture,) = fold.captures
    assert capture.identity.trade == "T-1"
    assert not fold.amendments
    assert not fold.valuations
    row, deltas = open_row(capture, T0, "tid-1", "alice")
    assert (row.trade, row.terms_version, row.amendment_count) == ("T-1", "T-1/v1", 0)
    assert (row.trade_date, row.effective_from) == (D0, D0)
    assert (row.transition_id, row.terms_transition_id) == ("tid-1", "tid-1")
    assert len(deltas) == 3
    assert {delta.period_start for delta in deltas} == {
        T0 + dt.timedelta(hours=h) for h in range(3)
    }
    assert all(delta.delta_mw == Decimal("10") for delta in deltas)


def test_buy_and_sell_net_to_zero() -> None:
    _, buy = _opened([captured("T-1", "buy"), terms("T-1")])
    _, sell = _opened([captured("T-2", "sell"), terms("T-2")])
    by_hour = [b.delta_mw + s.delta_mw for b, s in zip(buy, sell, strict=True)]
    assert by_hour == [Decimal(0)] * 3


def test_an_amendment_is_classified_and_moves_the_row() -> None:
    row, _ = _opened([captured(), terms(quantity="10", hours=3)])
    fold = fold_transition(
        [
            terms(version="T-1/v2", quantity="4", hours=2, effective_from=dt.date(2026, 7, 1)),
            supersedes("T-1/v2", "T-1/v1"),
        ],
        [],
    )
    assert not fold.captures
    (amendment,) = fold.amendments
    assert amendment.prior_version == "T-1/v1"
    advanced, deltas = advance_row(row, amendment, T0, "tid-2", "bob")
    # The capture's provenance stays; the terms' provenance moves.
    assert (advanced.transition_id, advanced.actor) == ("tid-1", "alice")
    assert (advanced.terms_transition_id, advanced.terms_actor) == ("tid-2", "bob")
    assert (advanced.terms_version, advanced.quantity, advanced.amendment_count) == (
        "T-1/v2",
        Decimal("4"),
        1,
    )
    assert advanced.trade_date == D0  # the first version's date, unmoved
    # Hours 0-1: 4 replaces 10 (-6); hour 2: 10 vacated (-10).
    assert [(d.period_start, d.delta_mw) for d in deltas] == [
        (T0, Decimal("-6")),
        (T0 + dt.timedelta(hours=1), Decimal("-6")),
        (T0 + dt.timedelta(hours=2), Decimal("-10")),
    ]


def test_an_earlier_dated_amendment_counts_but_does_not_move_the_row() -> None:
    # Unreachable under today's gate (an amendment is effective strictly
    # after its prior); the semantics are pinned so a relaxed gate finds
    # the projection already right.
    row, _ = _opened([captured(), terms(effective_from=dt.date(2026, 7, 1))])
    earlier = Amendment(
        models.TradeTermsClaim.from_named(
            terms(version="T-1/v0", quantity="99", effective_from=D0).args
        ),
        "T-1/v1",
    )
    advanced, deltas = advance_row(row, earlier, T0, "tid-2", "bob")
    assert (advanced.terms_version, advanced.quantity, advanced.amendment_count) == (
        "T-1/v1",
        Decimal("10"),
        1,
    )
    assert deltas == ()


def test_a_same_dated_amendment_is_refused() -> None:
    row, _ = _opened([captured(), terms()])
    tie = Amendment(
        models.TradeTermsClaim.from_named(terms(version="T-1/v2", effective_from=D0).args),
        "T-1/v1",
    )
    with pytest.raises(ProjectionError, match="share the effective date"):
        advance_row(row, tie, T0, "tid-2", "bob")


def test_a_valuation_becomes_one_row() -> None:
    fold = fold_transition(
        [
            envelopes.NamedClaim(
                "TradeValued",
                {
                    "org": "acme",
                    "book": "spec-de",
                    "trade": "T-1",
                    "curve_version": "crv-v1",
                    "terms_version": "T-1/v1",
                    "mtm": "55.00",
                },
            )
        ],
        [],
    )
    (valuation,) = fold.valuations
    assert (valuation.curve_version, valuation.terms_version, valuation.mtm) == (
        "crv-v1",
        "T-1/v1",
        Decimal("55.00"),
    )


def test_the_deliberately_ignored_predicates_fold_to_nothing() -> None:
    fold = fold_transition(
        [
            envelopes.NamedClaim(
                "MayCaptureTrade", {"actor": "alice", "org": "acme", "book": "spec-de"}
            ),
            envelopes.NamedClaim(
                "CurveRegistered",
                {
                    "org": "acme",
                    "market": "de-power",
                    "as_of": "2026-06-08",
                    "version": "crv-v2",
                    "payload_hash": "sha256:bb",
                },
            ),
            envelopes.NamedClaim(
                "CurveSupersedes", {"new_version": "crv-v2", "prior_version": "crv-v1"}
            ),
            envelopes.NamedClaim(
                "OfficialCurve",
                {
                    "org": "acme",
                    "market": "de-power",
                    "as_of": "2026-06-08",
                    "version": "crv-v2",
                },
            ),
        ],
        # correct_curve retracts the official pointer: a no-op here.
        [
            envelopes.NamedClaim(
                "OfficialCurve",
                {
                    "org": "acme",
                    "market": "de-power",
                    "as_of": "2026-06-08",
                    "version": "crv-v1",
                },
            )
        ],
    )
    assert fold == fold_transition([], [])


def test_refusals_are_loud() -> None:
    with pytest.raises(ProjectionError, match="append-only TradeValued"):
        fold_transition([], [envelopes.NamedClaim("TradeValued", {})])
    with pytest.raises(ProjectionError, match="no fold covers"):
        fold_transition([envelopes.NamedClaim("BrandNewPredicate", {})], [])
    with pytest.raises(ProjectionError, match="without TradeTerms"):
        fold_transition([captured()], [])
    # Terms with neither a capture nor a lineage claim beside them are a
    # shape the model cannot stage; the fold must not guess.
    with pytest.raises(ProjectionError, match="neither a capture's first version nor an amendment"):
        fold_transition([terms()], [])
    with pytest.raises(ProjectionError, match="arrived without its TradeTerms"):
        fold_transition([supersedes("T-1/v2", "T-1/v1")], [])
    with pytest.raises(ProjectionError, match="share an effective date"):
        fold_transition(
            [
                terms(version="T-1/v2", effective_from=dt.date(2026, 7, 1)),
                supersedes("T-1/v2", "T-1/v1"),
                terms(version="T-1/v3", effective_from=dt.date(2026, 7, 1)),
                supersedes("T-1/v3", "T-1/v2"),
            ],
            [],
        )
    with pytest.raises(ProjectionError, match="no position sign"):
        fold_transition([captured(direction="long"), terms()], [])


def test_a_capture_and_its_amendment_in_one_transaction_both_apply() -> None:
    # A `transact` may capture and amend in one decision: captures first,
    # then amendments in effective order.
    fold = fold_transition(
        [
            captured(),
            terms(),
            terms(version="T-1/v2", quantity="7", effective_from=dt.date(2026, 7, 1)),
            supersedes("T-1/v2", "T-1/v1"),
        ],
        [],
    )
    assert [c.terms.version for c in fold.captures] == ["T-1/v1"]
    assert [a.terms.version for a in fold.amendments] == ["T-1/v2"]


trade_ids = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-", min_size=1, max_size=8)
trade_quantities = st.decimals(
    min_value=Decimal("0.1"), max_value=Decimal("1000"), allow_nan=False, places=1
)


@st.composite
def trade_books(draw: st.DrawFn) -> list[tuple[str, str, Decimal, int]]:
    """A book of trades with distinct ids: (trade, direction, quantity,
    delivery hours), zero or more."""
    ids = draw(st.lists(trade_ids, max_size=6, unique=True))
    return [
        (
            tid,
            draw(st.sampled_from(["buy", "sell"])),
            draw(trade_quantities),
            draw(st.integers(min_value=1, max_value=48)),
        )
        for tid in ids
    ]


def _claims(specs: list[tuple[str, str, Decimal, int]]) -> list[envelopes.NamedClaim]:
    asserted: list[envelopes.NamedClaim] = []
    for trade, direction, quantity, hours in specs:
        asserted.extend((captured(trade, direction), terms(trade, str(quantity), hours)))
    return asserted


@given(trade_books())
def test_the_fold_conserves_trades_and_signed_hours(
    specs: list[tuple[str, str, Decimal, int]],
) -> None:
    # The read-side law as algebra: one blotter row per capture, one
    # position-hour per delivered hour, and the net MW correct FOR EACH
    # hour - not merely in total, which a fold that filed the right MW
    # under the wrong hour would also satisfy.
    fold = fold_transition(_claims(specs), [])
    assert len(fold.captures) == len(specs)
    assert {c.identity.trade for c in fold.captures} == {spec[0] for spec in specs}
    deltas = [d for c in fold.captures for d in open_row(c, T0, "tid", "a")[1]]
    assert len(deltas) == sum(hours for *_, hours in specs)

    actual: dict[dt.datetime, Decimal] = defaultdict(lambda: Decimal(0))
    for delta in deltas:
        actual[delta.period_start] += delta.delta_mw
    expected: dict[dt.datetime, Decimal] = defaultdict(lambda: Decimal(0))
    for _trade, direction, quantity, hours in specs:
        for hour in range(hours):
            expected[T0 + dt.timedelta(hours=hour)] += SIGN[direction] * quantity
    assert actual == expected


@st.composite
def version_chains(draw: st.DrawFn) -> list[models.TradeTermsClaim]:
    """One trade's versions with distinct, increasing effective dates,
    each with its own quantity and window."""
    n = draw(st.integers(min_value=1, max_value=6))
    days = sorted(
        draw(st.lists(st.integers(min_value=0, max_value=60), min_size=n, max_size=n, unique=True))
    )
    return [
        models.TradeTermsClaim(
            "acme",
            "T-1",
            f"T-1/v{i + 1}",
            draw(trade_quantities),
            Decimal("50"),
            T0 + dt.timedelta(hours=draw(st.integers(min_value=0, max_value=5))),
            T0 + dt.timedelta(hours=draw(st.integers(min_value=6, max_value=30))),
            D0 + dt.timedelta(days=day),
        )
        for i, day in enumerate(days)
    ]


@given(version_chains(), st.sampled_from(["buy", "sell"]))
def test_deltas_compose_and_the_row_ends_on_the_current_terms(
    chain: list[models.TradeTermsClaim], direction: str
) -> None:
    # Walking the chain one version at a time nets, hour by hour, to the
    # last version alone - so the position after N amendments equals
    # the position of a trade captured on those terms - and the row's
    # terms are the ones `current_terms` names.
    identity = models.TradeCapturedClaim("acme", "spec-de", "T-1", "cp", "de-power", direction)
    row, deltas = open_row(Capture(identity, chain[0]), T0, "tid-0", "a")
    net: dict[dt.datetime, Decimal] = defaultdict(lambda: Decimal(0))
    for delta in deltas:
        net[delta.period_start] += delta.delta_mw
    for i, version in enumerate(chain[1:], start=1):
        row, deltas = advance_row(
            row, Amendment(version, chain[i - 1].version), T0, f"tid-{i}", "a"
        )
        for delta in deltas:
            net[delta.period_start] += delta.delta_mw
    direct: dict[dt.datetime, Decimal] = defaultdict(lambda: Decimal(0))
    for delta in terms_delta(identity, None, chain[-1]):
        direct[delta.period_start] += delta.delta_mw
    assert {h: mw for h, mw in net.items() if mw} == {h: mw for h, mw in direct.items() if mw}
    assert row.terms() == current_terms(chain)
    assert row.amendment_count == len(chain) - 1


def test_the_wire_shape_decodes_into_the_fold() -> None:
    # As the NAMED audit tail carries it: args keyed by declared field,
    # values bare (the unit rides on the declaration, not the value), and
    # from_json decoding nothing - the typing is the generated model's
    # job. Decoded here through the real envelope rather than a
    # hand-built NamedClaim, so the fold is exercised against the shape
    # the binary actually emits.
    wire_terms = {
        "predicate": "TradeTerms",
        "args": {
            "org": "acme",
            "trade": "T-1",
            "version": "T-1/v1",
            "quantity": "10",
            "price": "86.25",
            "delivery_start": "2026-07-01T00:00:00Z",
            "delivery_end": "2026-07-01T03:00:00Z",
            "effective_from": "2026-06-30",
        },
    }
    fold = fold_transition([captured(), envelopes.NamedClaim.from_json(wire_terms)], [])
    _, deltas = open_row(fold.captures[0], T0, "tid", "a")
    assert deltas[0].delta_mw == Decimal("10")
    assert deltas[0].period_start == T0
