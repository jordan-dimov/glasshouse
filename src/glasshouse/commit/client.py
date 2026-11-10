"""Glasshouse's thin extension of the generated client.

The generated client covers the whole surface Glasshouse drives - the
operation timeout, batch `explain_on_reject`, the audit tail, the deploy
steps (`migrate`, `provision_indexes`), the tamper-evidence family
(`audit_verify`, `audit_checkpoint`, `audit_export` / `audit_verify_pack`)
and credential redaction in every raised error - all typed and under the
regenerate-and-diff drift gate. The bridge count is zero. What remains is
genuinely ours, nothing duplicated:

* **binary discovery** under `GLASSHOUSE_MORPHOLOG_BIN`;
* **the first-use compatibility check, switched on**: the constructor
  passes the package's stamps (`MORPHOLOG_VERSION`, `MODEL_HASH`), so the
  generated client asks `hash` once before the first call and refuses a
  binary of another version, or a programme with other rules, by name.
  Every Glasshouse client is a deployment client, so there is no
  unchecked construction path here on purpose;
* **`read`**, the typed per-predicate as-of read composing the generated
  named-claim surface;
* **`write_checkpoint`**, the exact-bytes anchor writer over the
  generated checkpoint envelope.

There was a third until v0.0.17: the writer-role assertion, three
overrides that carried `--writer-role` to every audit tail because
managed PostgreSQL hid the sessions the resume horizon was computed
from. The horizon is now the lowest audit order still held under a
lock, read from the lock table, so it needs no view of other sessions
and no assertion of who writes (morpholog#632): the flag is gone from
the binary and the overrides from here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar, Protocol, Self

from glasshouse.commit.morpholog_client import MODEL_HASH, MORPHOLOG_VERSION, envelopes
from glasshouse.commit.morpholog_client.adapter import Morpholog, MorphologNotCovered


class NamedClaimModel(Protocol):
    """The seam every generated read model exposes."""

    PREDICATE: ClassVar[str]

    @classmethod
    def from_named(cls, args: dict[str, object]) -> Self: ...


class GlasshouseClient(Morpholog):
    """The generated client plus Glasshouse's binary discovery, the
    first-use compatibility check switched on, the typed as-of read and
    the anchor writer."""

    def __init__(
        self,
        file: str,
        database_url: str,
        binary: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(
            file,
            database_url,
            binary or os.environ.get("GLASSHOUSE_MORPHOLOG_BIN"),
            timeout=timeout_seconds,
            # The generated `open_client()` in one constructor: the first
            # call asks `hash` and refuses a binary or programme that does
            # not match the stamps this package was generated with.
            expected_version=MORPHOLOG_VERSION,
            expected_model_hash=MODEL_HASH,
        )

    def write_checkpoint(
        self, path: str | Path, *, covering: str | None = None
    ) -> envelopes.CheckpointCreated | envelopes.CheckpointNoNewRows:
        """Record a checkpoint and write its JSON to `path` as an external
        anchor: the binary prints the checkpoint as JSON, and a later
        `audit_verify_pack(pack, anchor_file=path)` against it catches a
        rewrite that also rewrote the checkpoint table. Writes the exact
        bytes (after parsing once to validate) and returns the typed
        outcome. With `covering` (a transition id) the head must contain
        that transition, as on the generated `audit_checkpoint`; the
        refusal arrives on stdout as the `checkpoint_refusal` envelope and
        is raised as `MorphologNotCovered`, nothing written."""
        raw = self._invoke(
            "audit",
            "checkpoint",
            "--database-url",
            self.database_url,
            *self._opt("--covering", covering),
        )
        payload = json.loads(raw)
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise MorphologNotCovered(envelopes.CheckpointRefusal.from_json(payload))
        outcome = envelopes.parse_checkpoint_outcome(payload)
        Path(path).write_bytes(raw.encode("utf-8"))
        return outcome

    def read[C: NamedClaimModel](self, model: type[C], as_of: str | None = None) -> list[C]:
        """Read one predicate back through the named surface, decoded by
        declared kind into the generated read model, optionally as of a
        past transition (a transition id or an RFC 3339 timestamp)."""
        return [
            model.from_named(claim.args)
            for claim in self.claims_named(model.PREDICATE, as_of=as_of)
        ]
