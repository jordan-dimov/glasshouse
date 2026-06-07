"""The typed adapter over the morpholog CLI: a subprocess and JSON.

This is the only write path to governed state (law 1). One subprocess
call per method, against morpholog's pinned embedder contract: `init` to
provision (the schema travels inside the binary), `run --args-named` to
commit (with `--explain-on-reject` for same-snapshot diagnosis),
`explain --json` to ask without acting, `inspect claims` to read governed
state back (`--named` for field-name decoding by the substrate itself),
`hash` for the rules-identity of the model in force. Per-call subprocess
now (~9ms of process tax per upstream's own harness); `run --batch` when
it lands.

The outcome discrimination rule, verified against the real binary: every
decided result arrives on stdout, even when exit 1 flags a lawful
rejection; an operational failure leaves stdout empty with the error on
stderr. Empty stdout, not the exit code, separates `Rejected` from a
raise.

Deliberately absent until forced: outbox delivery (the needle has no
external effects), `schema --all` extraction (a build-step concern for
the codegen script, not a runtime one), and batch.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from glasshouse.commit.bases import ClaimRow, CommitRequest
from glasshouse.commit.envelope import (
    CLAIMS,
    NAMED_CLAIMS,
    OUTCOME,
    Claim,
    Explanation,
    NamedArgs,
    Outcome,
    named_wire,
)


class MorphologOperationalError(RuntimeError):
    """The CLI failed operationally: nothing was proposed, nothing was
    decided. Distinct from `Rejected`, which is a lawful outcome."""


@dataclass(frozen=True, slots=True)
class MorphologAdapter:
    """One rule model, one database, one binary."""

    model_file: Path
    database_url: str
    binary: str = "morpholog"
    timeout_seconds: float = 30.0

    def init(self, *, skip_if_exists: bool = False) -> bool:
        """Provision the morpholog schema from the binary itself (day
        zero only; never drops, never migrates). True if this call
        initialised it; False if it already existed and `skip_if_exists`
        let that pass. Without the flag, an existing schema raises."""
        flags = ["--skip-if-exists"] if skip_if_exists else []
        payload = self._invoke("init", *flags, "--database-url", self.database_url)
        match payload:
            case {"status": "initialised"}:
                return True
            case {"status": "already-initialised"}:
                return False
            case _:
                raise MorphologOperationalError(f"unexpected `init` output: {payload!r}")

    def run(
        self,
        transformation: str,
        *,
        actor: str,
        args: NamedArgs,
        explain_on_reject: bool = False,
    ) -> Outcome:
        """Propose one transition: `Committed | Rejected`, or a raise if
        nothing was decided. With `explain_on_reject`, a rejection
        carries its explanation, computed against the same pre-state the
        gates evaluated (no run-then-explain race)."""
        flags = ["--explain-on-reject"] if explain_on_reject else []
        return OUTCOME.validate_python(
            self._invoke("run", *self._proposal(transformation, actor, args), *flags)
        )

    def propose(
        self, request: CommitRequest, *, actor: str, explain_on_reject: bool = False
    ) -> Outcome:
        """`run` for a generated request model: the transformation name
        and the typed args travel together, so they cannot disagree."""
        return self.run(
            request.TRANSFORMATION,
            actor=actor,
            args=request.model_dump(),
            explain_on_reject=explain_on_reject,
        )

    def read[R: ClaimRow](self, row: type[R], *, as_of: str | None = None) -> list[R]:
        """`read_claims` for a generated read model: named rows parsed
        into typed fields (Decimal, date, aware datetime) by the model
        that knows each field's declared kind."""
        return [row.model_validate(args) for args in self.read_claims(row.PREDICATE, as_of=as_of)]

    def explain(self, transformation: str, *, actor: str, args: NamedArgs) -> Explanation:
        """Dry-run diagnosis against live state: the verdict, the failed
        gate or violated invariant, the directly-missing claims."""
        return Explanation.model_validate(
            self._invoke("explain", *self._proposal(transformation, actor, args), "--json")
        )

    def inspect_claims(self, *predicates: str, as_of: str | None = None) -> list[Claim]:
        """Currently-admitted claims of the named predicates, positional
        bare args. `as_of` is a transition id or an RFC 3339 timestamp.
        The claims table is the authority: an unknown predicate is an
        empty result, not an error."""
        flags = [flag for p in predicates for flag in ("--predicate", p)]
        if as_of is not None:
            flags += ["--as-of", as_of]
        return CLAIMS.validate_python(
            self._invoke("inspect", "claims", *flags, "--database-url", self.database_url)
        )

    def read_claims(
        self, predicate: str, *, as_of: str | None = None
    ) -> list[dict[str, JsonValue]]:
        """Claims of one predicate as named rows, decoded by the
        substrate (`--named`): the programme is the authority, so an
        undeclared predicate or programme/database skew raises upstream.
        Values are wire-true (decimals and dates stay strings); typed
        access belongs to the generated per-predicate models."""
        flags = ["--named", str(self.model_file), "--predicate", predicate]
        if as_of is not None:
            flags += ["--as-of", as_of]
        claims = NAMED_CLAIMS.validate_python(
            self._invoke("inspect", "claims", *flags, "--database-url", self.database_url)
        )
        return [claim.args for claim in claims]

    def model_hash(self) -> str:
        """The canonical rules-identity of the model file
        (`sha256:<hex>` over the canonical source: formatting and
        comments excluded). Records as `ruleset_version` in curve
        identities, evidence packs and generated-code headers."""
        payload = self._invoke("hash", str(self.model_file))
        match payload:
            case {"hash": str(digest)}:
                return digest
            case _:
                raise MorphologOperationalError(f"unexpected `hash` output: {payload!r}")

    def _proposal(self, transformation: str, actor: str, args: NamedArgs) -> list[str]:
        return [
            str(self.model_file),
            transformation,
            "--actor",
            actor,
            "--args-named",
            json.dumps(named_wire(args)),
            "--database-url",
            self.database_url,
        ]

    def _invoke(self, *args: str) -> JsonValue:
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MorphologOperationalError(f"could not run {self.binary!r}: {exc}") from exc
        if not proc.stdout.strip():
            raise MorphologOperationalError(
                f"`{self.binary} {' '.join(args)}` failed operationally "
                f"(exit {proc.returncode}):\n{proc.stderr.strip()}"
            )
        try:
            result: JsonValue = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise MorphologOperationalError(
                f"`{self.binary} {args[0]}` emitted non-JSON stdout: {proc.stdout[:200]!r}"
            ) from exc
        return result
