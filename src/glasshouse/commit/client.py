"""Glasshouse's thin extension of the generated client.

The generated client now covers the whole surface Glasshouse drives -
the operation timeout, batch `explain_on_reject`, `verify`, the audit
tail, the tamper-evidence family (`checkpoint`, `evidence_export` /
`evidence_verify`), and credential redaction in every raised error
message - all typed and under the regenerate-and-diff drift gate. So the
hand-written bridges are gone, including the last one (the `_invoke`
redaction seam: the generated client now masks `--database-url` in its
own messages, contract section 13 delivered). What remains is genuinely
ours, nothing duplicated:

* **binary discovery** under `GLASSHOUSE_MORPHOLOG_BIN`;
* **the writer-role assertion**, carried as deployment configuration
  rather than repeated at every call site (see below);
* **`read`**, the typed per-predicate as-of read composing the generated
  named-claim surface;
* **`export_evidence_pack`**, writing the binary's exact pack bytes to a
  file for offline verification (the generated `evidence_export` returns
  the typed pack for inspection, not a file).

The bridge count is zero again: the last one (the `views_schema` flag on
`verify`, our morpholog#192) was delivered upstream in #197 and deleted
here (contract section 19). `writer_roles` is not a bridge - the flag is
generated and typed (upstream #210) - it is a deployment property of the
connection, exactly like `database_url` and the timeout, so it belongs
on the client that holds those. Binding it here also makes it
impossible to forget: a new audit-tailing call site inherits the
assertion, where a per-call argument would pass every test on a
self-hosted database and fail only on managed PostgreSQL.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar, Protocol, Self

from glasshouse.commit.morpholog_client import envelopes
from glasshouse.commit.morpholog_client.adapter import Morpholog


class NamedClaimModel(Protocol):
    """The seam every generated read model exposes."""

    PREDICATE: ClassVar[str]

    @classmethod
    def from_named(cls, args: dict) -> Self:  # type: ignore[type-arg]
        ...


class GlasshouseClient(Morpholog):
    """The generated client plus Glasshouse's binary discovery, the
    configured writer-role assertion, the typed as-of read, and the
    offline pack export."""

    def __init__(
        self,
        file: str,
        database_url: str,
        binary: str | None = None,
        timeout_seconds: float | None = None,
        writer_roles: list[str] | None = None,
    ) -> None:
        super().__init__(
            file,
            database_url,
            binary or os.environ.get("GLASSHOUSE_MORPHOLOG_BIN"),
            timeout=timeout_seconds,
        )
        # The session roles that write morpholog.audit, asserted so the
        # resume horizon can be computed over their sessions alone. Empty
        # (the default, and every self-hosted deployment) leaves the
        # blessed all-sessions horizon exactly as it was.
        self.writer_roles = writer_roles or None

    def _asserted(self, writer_roles: list[str] | None) -> list[str] | None:
        """The generated `None` means "no flag"; here it means "whatever
        this deployment is configured with". An explicit empty list keeps
        the generated meaning - no flag, the all-sessions horizon - so a
        caller can still turn the assertion off for one call."""
        return self.writer_roles if writer_roles is None else writer_roles

    # The three surfaces that compute the resume horizon. Each keeps the
    # generated signature and semantics; the configured assertion is
    # simply the default a caller does not pass.
    def audit(self, after: str | None = None, *, writer_roles: list[str] | None = None) -> list:  # type: ignore[type-arg]
        return super().audit(after, writer_roles=self._asserted(writer_roles))

    def audit_named(
        self, after: str | None = None, *, writer_roles: list[str] | None = None
    ) -> list:  # type: ignore[type-arg]
        return super().audit_named(after, writer_roles=self._asserted(writer_roles))

    def checkpoint(
        self,
        signing_key: str | None = None,
        key_id: str | None = None,
        *,
        writer_roles: list[str] | None = None,
    ) -> envelopes.CheckpointCreated | envelopes.CheckpointNoNewRows:
        return super().checkpoint(signing_key, key_id, writer_roles=self._asserted(writer_roles))

    def write_checkpoint(
        self, path: str | Path
    ) -> envelopes.CheckpointCreated | envelopes.CheckpointNoNewRows:
        """Record a checkpoint and write its JSON to `path` as an external
        anchor: the binary prints the checkpoint as JSON, and a later
        `evidence_verify(pack, anchor_file=path)` against it catches a
        rewrite that also rewrote the checkpoint table. Writes the exact
        bytes (after parsing once to validate) and returns the typed
        outcome."""
        raw = self._invoke(
            "checkpoint",
            "--database-url",
            self.database_url,
            *self._repeat("--writer-role", self.writer_roles),
        )
        outcome = envelopes.parse_checkpoint_outcome(json.loads(raw))
        Path(path).write_bytes(raw.encode("utf-8"))
        return outcome

    def export_evidence_pack(self, path: str | Path, tree_size: int | None = None) -> None:
        """Write a complete-prefix evidence pack to `path` for offline
        verification. The binary writes the pack JSON to stdout; we write
        those exact bytes as explicit UTF-8 (the offline verifier
        recomputes roots from them) after parsing once to refuse a
        malformed pack loudly."""
        args = ["evidence", "export", "--database-url", self.database_url]
        if tree_size is not None:
            args.extend(["--tree-size", str(tree_size)])
        raw = self._invoke(*args)
        envelopes.EvidencePack.from_json(json.loads(raw))  # validate or raise
        Path(path).write_bytes(raw.encode("utf-8"))

    def read[C: NamedClaimModel](self, model: type[C], as_of: str | None = None) -> list[C]:
        """Read one predicate back through the named surface, decoded by
        declared kind into the generated read model, optionally as of a
        past transition (a transition id or an RFC 3339 timestamp)."""
        return [
            model.from_named(claim.args)
            for claim in self.claims_named(model.PREDICATE, as_of=as_of)
        ]
