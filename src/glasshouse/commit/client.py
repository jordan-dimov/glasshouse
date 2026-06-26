"""Glasshouse's thin extension of the generated client.

What lives here is either genuinely ours (binary discovery under
`GLASSHOUSE_MORPHOLOG_BIN`; `read`, the typed per-predicate as-of read
composing generated surfaces) or a documented bridge over a generated
client gap, filed upstream and deleted when delivered - the pattern
that retired the as-of gap (morpholog#135, delivered in #138):

* the optional operation timeout (morpholog#140, contract section 13):
  unset by default - imports legitimately run long - and set at the
  API boundary, where a hung binary must become a fast verdict; the
  override of `_audit_lines` extends the same bound to the audit tail,
  which the generated client runs outside `_invoke`;
* `propose_batch(..., explain_on_reject=)`: the CLI composes the flag
  with `--batch` and the envelope already parses per-row explanations,
  but the generated method exposes neither the flag nor a timeout
  (morpholog#141, contract section 14);
* `verify_ledger`: `morpholog verify` exists and is pinned, but the
  generated client has no surface for it (morpholog#141, contract
  section 14).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from typing import ClassVar, Protocol, Self

from glasshouse.commit.morpholog_client import envelopes
from glasshouse.commit.morpholog_client.adapter import Morpholog, MorphologError
from glasshouse.logging import get_logger

log = get_logger("glasshouse.commit")


def _redacted(args: Sequence[str]) -> str:
    """The invoked command as one string for a log event, with the
    database URL masked: it carries credentials, and a timeout event must
    name the operation without leaking the secret into the logs."""
    parts = list(args)
    for index, part in enumerate(parts):
        if part == "--database-url" and index + 1 < len(parts):
            parts[index + 1] = "***"
    return " ".join(parts)


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
            # A bounded operation that hangs is the API boundary's reason
            # to exist; record the full operation (URL redacted) before it
            # becomes a fast 503.
            log.warning(
                "commit.timeout", command=_redacted(args), timeout_seconds=self.timeout_seconds
            )
            raise MorphologError(
                f"`{_redacted(args)}` timed out after {self.timeout_seconds}s"
            ) from None
        if not proc.stdout.strip():
            # Redact the command: the args carry --database-url, and this
            # message is logged, propagated, and (at the API boundary) at
            # risk of being reflected to a client.
            raise MorphologError(f"`{_redacted(args)}`:\n{proc.stderr.strip()}")
        return proc.stdout

    def propose_batch(
        self, rows: Sequence[Mapping[str, object]], explain_on_reject: bool = False
    ) -> list[envelopes.BatchReceipt]:
        """The generated `propose_batch`, plus the per-row explanations
        and the timeout it lacks (contract section 14). Mirrors the
        generated semantics: one receipt per processed row in input
        order, exit 0 = every row processed, non-zero = operational
        abort with the receipts that did arrive named in the error."""
        ndjson = "".join(json.dumps(row) + "\n" for row in rows)
        command = [
            self.binary,
            "propose",
            self.file,
            "--batch",
            "-",
            "--database-url",
            self.database_url,
        ]
        if explain_on_reject:
            command.append("--explain-on-reject")
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                input=ndjson,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning(
                "commit.timeout",
                command="propose --batch",
                rows=len(rows),
                timeout_seconds=self.timeout_seconds,
            )
            raise MorphologError(f"batch timed out after {self.timeout_seconds}s") from None
        receipts = [
            envelopes.BatchReceipt.from_json(json.loads(line))
            for line in proc.stdout.splitlines()
            if line.strip()
        ]
        if proc.returncode != 0:
            raise MorphologError(
                f"batch aborted after {len(receipts)} receipt(s):\n{proc.stderr.strip()}"
            )
        return receipts

    def verify_ledger(self) -> dict:  # type: ignore[type-arg]
        """`morpholog verify`: replay the audit log and diff against the
        claims table. The divergent verdict arrives on stdout at exit 1,
        which is exactly `_invoke`'s discrimination rule; the JSON is
        `{"status": "consistent"|"divergent", ...divergence lists}`."""
        verdict = json.loads(self._invoke("verify", "--database-url", self.database_url))
        if not isinstance(verdict, dict):
            raise MorphologError(f"verify returned a non-object verdict: {verdict!r}")
        return verdict

    def _audit_lines(self, after: str | None, named: bool) -> list:  # type: ignore[type-arg]
        """The generated `_audit_lines`, plus the timeout (the #140
        family: the tail deliberately bypasses `_invoke`, so the
        `_invoke` override cannot bound it). An empty tail stays a
        lawful empty list; discrimination stays on the exit code."""
        argv = [self.binary, "inspect", "audit"]
        if after is not None:
            argv.extend(["--after", after])
        if named:
            argv.extend(["--named", self.file])
        argv.extend(["--database-url", self.database_url])
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning(
                "commit.timeout", command="inspect audit", timeout_seconds=self.timeout_seconds
            )
            raise MorphologError(f"inspect audit timed out after {self.timeout_seconds}s") from None
        if proc.returncode != 0:
            raise MorphologError(
                f"inspect audit failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
            )
        return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]

    def read[C: NamedClaimModel](self, model: type[C], as_of: str | None = None) -> list[C]:
        """Read one predicate back through the named surface, decoded by
        declared kind into the generated read model, optionally as of a
        past transition (a transition id or an RFC 3339 timestamp)."""
        return [
            model.from_named(claim.args)
            for claim in self.claims_named(model.PREDICATE, as_of=as_of)
        ]
