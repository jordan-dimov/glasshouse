"""Glasshouse's thin extension of the generated client.

Two things live here, both genuinely ours rather than gaps in the
generated surface (the as-of gap this module used to bridge was filed
as morpholog#135 and delivered upstream in #138):

* binary discovery under Glasshouse's own environment name
  (`GLASSHOUSE_MORPHOLOG_BIN`, falling back to the generated client's
  `MORPHOLOG_BIN`-then-PATH resolution), so one name works across the
  app, the docs and the generated layer;
* `read`, the typed per-predicate read: the generated `claims_named`
  surface composed with the generated read models, so consumers get
  frozen typed rows (optionally as of a past transition) in one call.
"""

from __future__ import annotations

import os
from typing import ClassVar, Protocol, Self

from glasshouse.commit.morpholog_client.adapter import Morpholog


class NamedClaimModel(Protocol):
    """The seam every generated read model exposes."""

    PREDICATE: ClassVar[str]

    @classmethod
    def from_named(cls, args: dict) -> Self:  # type: ignore[type-arg]
        ...


class GlasshouseClient(Morpholog):
    """The generated client plus Glasshouse's binary discovery and the
    typed as-of read."""

    def __init__(self, file: str, database_url: str, binary: str | None = None) -> None:
        super().__init__(file, database_url, binary or os.environ.get("GLASSHOUSE_MORPHOLOG_BIN"))

    def read[C: NamedClaimModel](self, model: type[C], as_of: str | None = None) -> list[C]:
        """Read one predicate back through the named surface, decoded by
        declared kind into the generated read model, optionally as of a
        past transition (a transition id or an RFC 3339 timestamp)."""
        return [
            model.from_named(claim.args)
            for claim in self.claims_named(model.PREDICATE, as_of=as_of)
        ]
