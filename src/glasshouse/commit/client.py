"""Glasshouse's thin extension of the generated client.

Three things live here (the as-of gap this module used to bridge was
filed as morpholog#135 and delivered upstream in #138):

* binary discovery under Glasshouse's own environment name
  (`GLASSHOUSE_MORPHOLOG_BIN`, falling back to the generated client's
  `MORPHOLOG_BIN`-then-PATH resolution), so one name works across the
  app, the docs and the generated layer;
* `read`, the typed per-predicate read: the generated `claims_named`
  surface composed with the generated read models, so consumers get
  frozen typed rows (optionally as of a past transition) in one call;
* an optional operation timeout the generated `_invoke` lacks
  (contract doc section 13, to file upstream): unset by default -
  imports legitimately run long - and set at the API boundary, where a
  hung binary must become a fast verdict, never a stuck request.
"""

from __future__ import annotations

import os
import subprocess
from typing import ClassVar, Protocol, Self

from glasshouse.commit.morpholog_client.adapter import Morpholog, MorphologError


class NamedClaimModel(Protocol):
    """The seam every generated read model exposes."""

    PREDICATE: ClassVar[str]

    @classmethod
    def from_named(cls, args: dict) -> Self:  # type: ignore[type-arg]
        ...


class GlasshouseClient(Morpholog):
    """The generated client plus Glasshouse's binary discovery, the
    typed as-of read, and an optional operation timeout."""

    def __init__(
        self,
        file: str,
        database_url: str,
        binary: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(file, database_url, binary or os.environ.get("GLASSHOUSE_MORPHOLOG_BIN"))
        self.timeout_seconds = timeout_seconds

    def _invoke(self, *args: str, stdin: str | None = None) -> str:
        """The generated `_invoke`, plus the timeout. Mirrors the
        generated semantics exactly - decided results arrive on stdout,
        empty stdout raises - and deletes the day the generated client
        grows a timeout of its own."""
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                input=stdin,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise MorphologError(
                f"`{' '.join(args)}` timed out after {self.timeout_seconds}s"
            ) from None
        if not proc.stdout.strip():
            raise MorphologError(f"`{' '.join(args)}`:\n{proc.stderr.strip()}")
        return proc.stdout

    def read[C: NamedClaimModel](self, model: type[C], as_of: str | None = None) -> list[C]:
        """Read one predicate back through the named surface, decoded by
        declared kind into the generated read model, optionally as of a
        past transition (a transition id or an RFC 3339 timestamp)."""
        return [
            model.from_named(claim.args)
            for claim in self.claims_named(model.PREDICATE, as_of=as_of)
        ]
