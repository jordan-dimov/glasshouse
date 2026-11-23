"""Every act either refuses a declared agent or is on a reviewed list.

The check against agents (`not AgentActor(org, actor)`) is written by hand
on each act people use, so the next act added can forget it, as
`admit_valuation` was found to lack it only when the rules were read this
way. This test reads the control matrix the binary itself derives from the
rules (`inspect controls --json`) rather than a list kept here, so a new
act fails it until it either gains the check or is added below with the
reason an agent may run it.

There is no generated method for `inspect controls`, so this is a
test-only call of the binary, recorded in the contract's section 44.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from glasshouse.commit import MODEL_FILE
from tests.support import BINARY

pytestmark = pytest.mark.skipif(not BINARY.exists(), reason=f"needs a morpholog binary at {BINARY}")

# Acts with no check against agents, each with the reason it is safe or
# intended. Reviewed 23/11/2026.
AN_AGENT_MAY_RUN = {
    # The mandate is the agent's authority; its gates are the bounds.
    "trade_under_mandate": "the act agents trade by",
    # Anyone the mandate names may stop it at once, the agent included.
    "suspend_mandate": "an agent may suspend its own mandate",
    # Bookkeeping: it only ends instructions whose time has passed.
    "lapse_overdue_instructions": "frees room only by the rule's own clock",
    # Decided 23/11/2026: valuing a trade records a mark against the
    # official curve and the terms in force; it moves no position.
    "admit_valuation": "an agent may value trades when granted MayValueTrade",
    # The founding acts run before any agent can be declared.
    "establish_ledger": "runs once, before any organisation",
    "establish_organisation": "needs MayEstablishOrganisations, the ledger root",
}

# Acts gated by MayGrantAuthority, which no agent can hold:
# grant_grant_authority refuses an agent principal and declare_agent
# refuses an actor who already holds it. The second test proves both.
NEEDS_GRANT_AUTHORITY = {
    "grant_capture_authority",
    "grant_curve_authority",
    "grant_valuation_authority",
    "grant_instruct_authority",
    "grant_approve_authority",
    "grant_fill_authority",
    "grant_breach_authority",
    "grant_limit_authority",
    "grant_date_authority",
    "require_approval",
    "declare_market",
}


def _controls() -> dict[str, list[dict[str, object]]]:
    run = subprocess.run(
        [str(BINARY), "inspect", "controls", "--json", str(MODEL_FILE)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    matrix = json.loads(run.stdout)
    return {t["transformation"]: t["gates"] for t in matrix["transformations"]}


def _consults(gates: list[dict[str, object]]) -> set[str]:
    return {str(p) for g in gates for p in g["consults"]}  # type: ignore[attr-defined]


def test_every_act_refuses_agents_or_is_reviewed() -> None:
    controls = _controls()
    unchecked = {act for act, gates in controls.items() if "AgentActor" not in _consults(gates)}
    reviewed = set(AN_AGENT_MAY_RUN) | NEEDS_GRANT_AUTHORITY
    assert unchecked - reviewed == set(), "act without the agent check and not reviewed"
    # A reviewed entry for an act that now has the check, or no longer
    # exists, is stale: keep the list honest in both directions.
    assert reviewed - unchecked == set(), "reviewed act that is gone or now checked"


def test_grant_authority_can_never_reach_an_agent() -> None:
    controls = _controls()
    for act in NEEDS_GRANT_AUTHORITY:
        assert "MayGrantAuthority" in _consults(controls[act]), act
    grant = {str(g["name"]): str(g["condition"]) for g in controls["grant_grant_authority"]}
    assert "AgentActor" in grant["principal_is_not_an_agent"]
    declare = {str(g["name"]): str(g["condition"]) for g in controls["declare_agent"]}
    assert "MayGrantAuthority" in declare["agent_cannot_grant"]
