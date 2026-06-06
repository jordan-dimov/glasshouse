"""The typed adapter over the morpholog CLI: a subprocess and JSON.

This is the only write path to governed state (law 1). Five methods, one
subprocess call each, against morpholog's pinned embedder contract:
`run --args-named` to commit, `explain --json` to diagnose,
`inspect claims --predicate` / `inspect predicates` to read governed
state back. Per-call subprocess now (~9ms of process tax per upstream's
own harness); `run --batch` when it lands.

The outcome discrimination rule, verified against the real binary: every
decided result arrives on stdout, even when exit 1 flags a lawful
rejection; an operational failure leaves stdout empty with the error on
stderr. Empty stdout, not the exit code, separates `Rejected` from a
raise.

Deliberately absent until forced: outbox delivery (the needle has no
external effects), `schema` extraction (a build-step concern for the
codegen script, not a runtime one), and batch.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from glasshouse.commit.envelope import (
    CLAIMS,
    OUTCOME,
    PREDICATE_DECLS,
    BareValue,
    Claim,
    Explanation,
    NamedArgs,
    Outcome,
    PredicateDecl,
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

    def run(self, transformation: str, *, actor: str, args: NamedArgs) -> Outcome:
        """Propose one transition: `Committed | Rejected`, or a raise if
        nothing was decided."""
        return OUTCOME.validate_python(
            self._invoke("run", *self._proposal(transformation, actor, args))
        )

    def explain(self, transformation: str, *, actor: str, args: NamedArgs) -> Explanation:
        """Dry-run diagnosis against live state: the verdict, the failed
        gate or violated invariant, the directly-missing claims."""
        return Explanation.model_validate(
            self._invoke("explain", *self._proposal(transformation, actor, args), "--json")
        )

    def inspect_claims(self, *predicates: str, as_of: str | None = None) -> list[Claim]:
        """Currently-admitted claims of the named predicates, positional
        bare args. `as_of` is a transition id or an RFC 3339 timestamp.
        An unknown predicate is an empty result, not an error: the claims
        table is the authority, not any one programme's vocabulary."""
        flags = [flag for p in predicates for flag in ("--predicate", p)]
        if as_of is not None:
            flags += ["--as-of", as_of]
        return CLAIMS.validate_python(
            self._invoke("inspect", "claims", *flags, "--database-url", self.database_url)
        )

    def read_claims(
        self, predicate: str, *, as_of: str | None = None
    ) -> list[dict[str, BareValue]]:
        """Claims of one predicate as named rows, decoded via the declared
        vocabulary with an arity guard against programme/database skew."""
        decls = {decl.name: decl.field_names for decl in self.inspect_predicates()}
        if predicate not in decls:
            raise MorphologOperationalError(
                f"predicate {predicate!r} is not declared in {self.model_file}; "
                f"declared: {sorted(decls)}"
            )
        fields = decls[predicate]
        rows = []
        for claim in self.inspect_claims(predicate, as_of=as_of):
            if len(claim.args) != len(fields):
                raise MorphologOperationalError(
                    f"{predicate}: claim arity {len(claim.args)} != declared arity "
                    f"{len(fields)} (programme/database skew); fields={fields}"
                )
            rows.append(dict(zip(fields, claim.args, strict=True)))
        return rows

    def inspect_predicates(self) -> list[PredicateDecl]:
        return PREDICATE_DECLS.validate_python(
            self._invoke("inspect", "predicates", str(self.model_file))
        )

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
