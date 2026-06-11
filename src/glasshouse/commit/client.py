"""The one read the needle requires that the generated client lacks.

`GlasshouseClient` is the generated subprocess adapter plus a typed
per-predicate read with `--as-of` (a transition id or an RFC 3339
timestamp): "as-of the registration transition, v1 was the official
curve" is a headline Glasshouse query, and the binary supports it while
the generated `claims`/`claims_named` do not yet expose it. The gap is
filed upstream as morpholog#135 (contract doc section 11); this
subclass deletes the day the generated surface grows the parameter.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, Self

from glasshouse.commit.morpholog_client.adapter import Morpholog, MorphologError
from glasshouse.commit.morpholog_client.envelopes import NamedClaim


class NamedClaimModel(Protocol):
    """The seam every generated read model exposes."""

    PREDICATE: ClassVar[str]

    @classmethod
    def from_named(cls, args: dict) -> Self:  # type: ignore[type-arg]
        ...


class GlasshouseClient(Morpholog):
    """The generated client, extended with the as-of typed read."""

    def read[C: NamedClaimModel](self, model: type[C], as_of: str | None = None) -> list[C]:
        """Read one predicate back through the named surface, decoded by
        declared kind into the generated read model, optionally as of a
        past transition."""
        args = [
            "inspect",
            "claims",
            "--predicate",
            model.PREDICATE,
            "--named",
            self.file,
            "--database-url",
            self.database_url,
        ]
        if as_of is not None:
            args += ["--as-of", as_of]
        payload = self._json(*args)
        if not isinstance(payload, list):
            raise MorphologError(f"named read returned a non-array payload: {payload!r}")
        return [model.from_named(NamedClaim.from_json(row).args) for row in payload]
