"""The hand-written half of the generated client contract.

Generated request and read models (see `generated.py`) inherit these
two bases; the adapter's `propose` and `read` are typed against them.
They are deliberately the *only* hand-written piece of the generated
surface: everything else is a projection of the model file via the
schema manifest, and regenerating it never touches this module.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class CommitRequest(BaseModel):
    """One transformation's arguments, typed. `model_dump()` yields the
    bare named args the adapter encodes for `--args-named`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    TRANSFORMATION: ClassVar[str]


class ClaimRow(BaseModel):
    """One predicate's claim as a typed row: parses the wire-true
    strings of a named read into Decimal, date and aware datetime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    PREDICATE: ClassVar[str]
