"""A rule change judged against history before it ships.

Since v0.0.14 every audit row names the programme that admitted it, so a
ledger can outlive a change of rules that does not reshape a predicate.
What makes that safe is knowing, before deploying, whether the new rules
would have refused anything the old ones admitted. `morpholog evaluate`
answers it: it replays a record under a candidate's invariants and
reports, per invariant, the admitted transitions it would have refused.

The record here is the demo's history as the previous rulebook admitted
it (`tests/fixtures/demo-history-972e70fd.ndjson`: the seed under
MODEL_HASH 972e70fd, exported by v0.0.15 as a complete-prefix pack), so
the gate runs offline with no database. A candidate that would refuse
any of it fails by name. `evaluate` replays invariants only; the gates
are `rehearse`'s (v0.0.17), which re-decides every recorded proposal
through the ordinary kernel under the candidate, gates, bindings and
arithmetic included, and says which rule would refuse which transition.

The pack is format 17. A re-pin that changes the pack format makes this
fail as `unsupported_pack` rather than pass: re-export the fixture with
the pinned binary (`glasshouse seed --reset`, `checkpoint`,
`evidence-export`) under the rulebook then on main, and rename it.
"""

from pathlib import Path

from glasshouse.commit import MODEL_FILE, MODEL_HASH, GlasshouseClient
from tests.support import BINARY, DB, needs_live_stack

pytestmark = needs_live_stack

HISTORY = Path(__file__).parents[1] / "fixtures" / "demo-history-972e70fd.ndjson"


def test_no_invariant_would_refuse_the_history_the_previous_rules_admitted() -> None:
    # Through the generated `evaluate(pack=)` (v0.0.16): a typed
    # `ScoreReport`, read offline from the pack; the database URL on the
    # client is never opened in pack mode.
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    report = client.evaluate(pack=str(HISTORY))
    assert report.program_hash == MODEL_HASH
    assert report.transitions_replayed == 32
    refusing = {
        score.invariant: score.refused_transitions
        for score in report.invariants
        if score.would_refuse or not score.initially_holds
    }
    assert refusing == {}
    # Every invariant of the programme was scored, the new ones included.
    assert len(report.invariants) == 19


def test_the_gates_refuse_only_what_a_rule_change_meant_to_refuse() -> None:
    # `rehearse(pack=)`: every proposal of the recorded history re-decided
    # under today's programme. The record predates the armed ledger root
    # (#94), so its ten grants by an unestablished root are refused by
    # `may_grant_authority`, which is that change doing its job. Anything
    # else here is a gate tightened by accident: the limit gate and its
    # whole-hour and forward gates admit every capture and amendment the
    # old rules admitted, with the same claims.
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    report = client.rehearse(pack=str(HISTORY))
    assert report.program_hash == MODEL_HASH
    assert report.transitions_replayed == 32
    assert {(refusal.rule, refusal.would_refuse) for refusal in report.refusals} == {
        ("may_grant_authority", 10)
    }
    totals = report.totals
    assert (totals.same, totals.would_reject) == (22, 10)
    # The exact-arithmetic check the v0.0.17 upgrade asks for: no recorded
    # proposal errors where `+`, `-` and `*` used to round, none decides
    # to other claims, and none is beyond replaying.
    assert (totals.different_delta, totals.would_error, totals.unreplayable) == (0, 0, 0)
