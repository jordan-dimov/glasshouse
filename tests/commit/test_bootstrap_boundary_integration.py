"""The bootstrap boundary: who may bring authority into being.

Delegation was already governed (every grant needs `may_grant_authority`).
This pins the step before it. The ledger's root actor is established once
(`establish_ledger`) and restricted in the same act to the login that
established it, using Morpholog's reserved assertion policy, so no other
database login can assert it, and no actor but the root can establish an
organisation. The second login here is a superuser on purpose: the
boundary is the login a proposal arrives on, not its privileges, and the
adapter refuses before evaluating anything, so nothing is recorded.

What this does not cover is stated in the model: a deployment whose
services share one login (the demo, until glasshouse#85) shares the
root too. The guarantee is only as distinct as the credentials.
"""

from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, Rejected, models
from glasshouse.commit.morpholog_client.adapter import MorphologRequestError
from tests.support import BINARY, DB, login_role, needs_live_stack, provision

pytestmark = needs_live_stack

UNTRUSTED = "glasshouse_untrusted_login"
ORG = "acme-energy"


@pytest.fixture
def untrusted_url() -> Iterator[str]:
    engine = provision()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(sa.text(f"DROP ROLE IF EXISTS {UNTRUSTED}"))
        connection.execute(sa.text(f"CREATE ROLE {UNTRUSTED} LOGIN SUPERUSER PASSWORD 'untrusted'"))
    try:
        url = make_url(DB).set(username=UNTRUSTED, password="untrusted")
        yield url.render_as_string(hide_password=False)
    finally:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(sa.text(f"DROP ROLE IF EXISTS {UNTRUSTED}"))
        engine.dispose()


def test_only_the_provisioning_login_can_bring_authority_into_being(untrusted_url: str) -> None:
    trusted = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    untrusted = GlasshouseClient(str(MODEL_FILE), untrusted_url, binary=str(BINARY))
    assert trusted.init().status == "initialised"
    root = trusted.submit(models.EstablishLedgerRequest(login_role=login_role()), actor="bootstrap")
    assert isinstance(root, Committed), root
    recorded = len(trusted.audit())

    # Another login asserting the root is refused before evaluation.
    with pytest.raises(MorphologRequestError) as asserted:
        untrusted.submit(models.EstablishOrganisationRequest(org=ORG), actor="bootstrap")
    assert asserted.value.code == "actor_assertion_unauthorised"
    # Any other actor from it is refused by the gate, and so is a second root.
    squat = untrusted.submit(models.EstablishOrganisationRequest(org=ORG), actor="mallory")
    assert isinstance(squat, Rejected), squat
    assert squat.rule == "may_establish_organisations"
    usurp = untrusted.submit(models.EstablishLedgerRequest(login_role=UNTRUSTED), actor="mallory")
    assert isinstance(usurp, Rejected), usurp
    assert usurp.rule == "ledger_is_new"
    # Nothing came into being and the impersonation left no trace.
    assert trusted.read(models.MayGrantAuthorityClaim) == []
    assert len(trusted.audit()) == recorded

    # The provisioning login can, and a delegate is bound to its own login.
    for request in (
        models.EstablishOrganisationRequest(org=ORG),
        models.GrantGrantAuthorityRequest(principal="ops", org=ORG, login_role=login_role()),
    ):
        assert isinstance(trusted.submit(request, actor="bootstrap"), Committed)
    with pytest.raises(MorphologRequestError) as delegate:
        untrusted.submit(
            models.GrantCaptureAuthorityRequest(principal="mallory", org=ORG, book="spec-de"),
            actor="ops",
        )
    assert delegate.value.code == "actor_assertion_unauthorised"
