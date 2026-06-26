"""The commit zone: the only write path to governed state.

This package wraps the morpholog binary behind the typed client the
binary itself generates (`morpholog generate python-client`), committed
byte-exact under `morpholog_client/` and drift-checked by regenerating
against the live binary in the integration leg. The client returns
``Committed | Rejected`` as typed outcomes and raises ``MorphologError``
on operational failure, so rejection-vs-failure confusion stays
unrepresentable. `GlasshouseClient` adds the one hand-written read the
generated surface lacks (the as-of query; filed upstream).

The one absolute rule of the codebase: writes to governed state only ever
go through this package. No ORM writes, no raw SQL writes, no exceptions.
"""

from pathlib import Path

from glasshouse.commit.client import GlasshouseClient, NamedClaimModel
from glasshouse.commit.morpholog_client import (
    MODEL_HASH,
    PROGRAM,
    envelopes,
    models,
    values,
)
from glasshouse.commit.morpholog_client.adapter import MorphologError
from glasshouse.commit.morpholog_client.envelopes import Committed, Rejected
from glasshouse.commit.views import VIEWS_FILE, VIEWS_SCHEMA, apply_views, views_model_hash

# The rule model ships inside the package; the client, the generator and
# the deployment all point at this one file.
MODEL_FILE = Path(__file__).parent / "glasshouse.morph"

# A commit's two lawful endings; operational failure raises instead.
type Outcome = Committed | Rejected

__all__ = [
    "MODEL_FILE",
    "MODEL_HASH",
    "PROGRAM",
    "VIEWS_FILE",
    "VIEWS_SCHEMA",
    "Committed",
    "GlasshouseClient",
    "MorphologError",
    "NamedClaimModel",
    "Outcome",
    "Rejected",
    "apply_views",
    "envelopes",
    "models",
    "values",
    "views_model_hash",
]
