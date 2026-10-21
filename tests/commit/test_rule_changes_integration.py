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
any of it fails by name. Gates are not scored: `evaluate` replays
invariants only, so a tightened gate still needs its own test.

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
    assert len(report.invariants) == 17
