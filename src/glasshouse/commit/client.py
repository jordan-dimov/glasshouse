"""Glasshouse's thin extension of the generated client.

The generated client now covers the whole surface Glasshouse drives -
the operation timeout, batch `explain_on_reject`, `audit_verify`, the
audit tail, the tamper-evidence family (`audit_checkpoint`,
`audit_export` / `audit_verify_pack`), and credential redaction in every
raised error message - all typed and under the regenerate-and-diff drift
gate. (Upstream v0.0.8 gathered that family under one `morpholog audit`
command group, so the generated methods gained their `audit_` prefix;
the rename is absorbed by regeneration, but see `audit_checkpoint`
below - an override that stops overriding is silent.) So the
hand-written bridges are gone, including the last one (the `_invoke`
redaction seam: the generated client now masks `--database-url` in its
own messages, contract section 13 delivered). What remains is genuinely
ours, nothing duplicated:

* **binary discovery** under `GLASSHOUSE_MORPHOLOG_BIN`;
* **the writer-role assertion**, carried as deployment configuration
  rather than repeated at every call site (see below);
* **`read`**, the typed per-predicate as-of read composing the generated
  named-claim surface;
* **`provision_indexes`**, the one command the generated client does
  not spell: `morpholog provision indexes`, which since v0.0.12 builds
  the managed indexes every keyed load and compiled check seeks
  through. It prints a plan, not JSON, so the parser lives here.

The bridge count is one: `provision_indexes` is a hand-built argv for a
surface the generator does not emit (recorded in contract section 25,
deleted the day a generated method lands - the pattern every earlier
bridge followed). The pack export that used to live here is gone: since
v0.0.12 the generated `audit_export(path)` streams the pack to a file
itself. `writer_roles` is not a bridge - the flag is
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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol, Self, override

from glasshouse.commit.morpholog_client import envelopes
from glasshouse.commit.morpholog_client.adapter import Morpholog, MorphologError, _redact_argv

# The actions `provision indexes` prints, one per managed index, exactly
# as the binary's help lists them. Anything else on a plan line is drift.
_INDEX_ACTIONS = ("KEEP", "CREATE", "REPAIR INVALID", "SATISFIED EXTERNALLY", "STALE", "CONFLICT")
_PLAN_LINE = re.compile(rf"^({'|'.join(_INDEX_ACTIONS)})\s+(\S+)\s*(.*)$")


@dataclass(frozen=True)
class IndexAction:
    """One line of the plan: what `provision indexes` did (or, on a dry
    run, would do) to one managed index."""

    action: str
    index: str
    detail: str


@dataclass(frozen=True)
class IndexPlan:
    """The reconciled index set for the programme: every managed index
    with its action, and whether the plan was applied or only printed."""

    actions: tuple[IndexAction, ...]
    applied: bool

    def count(self, action: str) -> int:
        return sum(1 for entry in self.actions if entry.action == action)

    def summary(self) -> str:
        """The actions that occurred, in the binary's order, as counts."""
        present = [
            f"{self.count(action)} {action.lower()}"
            for action in _INDEX_ACTIONS
            if self.count(action)
        ]
        return ", ".join(present) or "nothing to provision"


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
    #
    # These names must track the generated ones exactly: an override that
    # no longer matches its base silently becomes a dead method, and the
    # deployment's writer-role assertion stops reaching the binary with
    # nothing failing - the managed-PostgreSQL horizon bug back again, and
    # invisible to every self-hosted test. The v0.0.8 `audit` grouping
    # renamed `checkpoint` to `audit_checkpoint` and did exactly that.
    #
    # `@override` is the guard that failure earned: mypy now refuses a
    # method here that overrides nothing, so the next regrouping is a
    # type error at the moment the client is regenerated rather than a
    # dead function nobody notices. `tests/commit/test_client.py` still
    # pins the argv each one emits - the decorator proves the method is
    # reached, the test proves it sends the flag.
    @override
    def audit(self, after: str | None = None, *, writer_roles: list[str] | None = None) -> list:  # type: ignore[type-arg]
        return super().audit(after, writer_roles=self._asserted(writer_roles))

    @override
    def audit_named(
        self, after: str | None = None, *, writer_roles: list[str] | None = None
    ) -> list:  # type: ignore[type-arg]
        return super().audit_named(after, writer_roles=self._asserted(writer_roles))

    @override
    def audit_checkpoint(
        self,
        signing_key: str | None = None,
        key_id: str | None = None,
        *,
        writer_roles: list[str] | None = None,
        witnesses: list[str] | None = None,
    ) -> envelopes.CheckpointCreated | envelopes.CheckpointNoNewRows:
        return super().audit_checkpoint(
            signing_key, key_id, writer_roles=self._asserted(writer_roles), witnesses=witnesses
        )

    def write_checkpoint(
        self, path: str | Path
    ) -> envelopes.CheckpointCreated | envelopes.CheckpointNoNewRows:
        """Record a checkpoint and write its JSON to `path` as an external
        anchor: the binary prints the checkpoint as JSON, and a later
        `audit_verify_pack(pack, anchor_file=path)` against it catches a
        rewrite that also rewrote the checkpoint table. Writes the exact
        bytes (after parsing once to validate) and returns the typed
        outcome."""
        raw = self._invoke(
            "audit",
            "checkpoint",
            "--database-url",
            self.database_url,
            *self._repeat("--writer-role", self.writer_roles),
        )
        outcome = envelopes.parse_checkpoint_outcome(json.loads(raw))
        Path(path).write_bytes(raw.encode("utf-8"))
        return outcome

    def provision_indexes(self, *, prune: bool = False, dry_run: bool = False) -> IndexPlan:
        """Reconcile the managed indexes this programme's keyed loads and
        compiled checks seek through (`morpholog provision indexes`).
        Correctness never depends on them - an unindexed keyed read scans
        the predicate and holds the lock a whole-predicate read held -
        so this is provisioning, run after `init` and after `migrate`
        (a migration can rekey every index, as v0.0.12's did). `prune`
        also drops managed indexes no longer required, which is right
        when this is the only programme on the database, as it is for
        Glasshouse. Builds run concurrently; the claims table stays
        writable. A conflict needs an operator and raises."""
        args = ["provision", "indexes", self.file, "--database-url", self.database_url]
        if prune:
            args.append("--prune")
        if dry_run:
            args.append("--dry-run")
        proc = self._run(args, timeout=self.timeout)
        if proc.returncode != 0:
            raise MorphologError(
                f"`{_redact_argv(args)}`:\n{proc.stdout}{self._redact_stderr(proc.stderr)}"
            )
        actions: list[IndexAction] = []
        applied: bool | None = None
        for line in proc.stdout.splitlines():
            if not line.strip() or line.startswith("program:"):
                continue
            if line == "applied":
                applied = True
                continue
            if line.startswith("dry run:"):
                applied = False
                continue
            matched = _PLAN_LINE.match(line)
            if matched is None:
                raise MorphologError(
                    f"`{_redact_argv(args)}`: not a provision plan line: {line!r} - the "
                    "binary's plan format has drifted past this client"
                )
            actions.append(IndexAction(matched[1], matched[2], matched[3]))
        if applied is None:
            raise MorphologError(f"`{_redact_argv(args)}`: the plan ended without a verdict line")
        return IndexPlan(tuple(actions), applied)

    def read[C: NamedClaimModel](self, model: type[C], as_of: str | None = None) -> list[C]:
        """Read one predicate back through the named surface, decoded by
        declared kind into the generated read model, optionally as of a
        past transition (a transition id or an RFC 3339 timestamp)."""
        return [
            model.from_named(claim.args)
            for claim in self.claims_named(model.PREDICATE, as_of=as_of)
        ]
