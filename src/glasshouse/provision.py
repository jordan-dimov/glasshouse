"""`glasshouse provision`: non-destructive first-boot provisioning.

The idempotent steps a fresh (or already-provisioned) database needs
before the web service can serve: migrate the app schema to head,
initialise the governed schema if absent (`init --skip-if-exists`),
bring that governed schema up to the binary's own version (`migrate`),
apply the sealed inspection views, and prove the audit tail can be read
at all. This is the web service's pre-deploy
command (DESIGN section 13: migrations and the Morpholog bootstrap run
in a pre-deploy step, never at app startup) and is safe to run on every
deploy: both migrations no-op when current, the init skips an existing
schema, and the committed view script re-applies and re-seals.

`--least-privilege` passes the upstream provisioning floor through
(reader/writer group roles; the report names the membership grants only
the operator can decide - printed, never executed). It needs CREATEROLE
on the connecting role, which managed hosts may not grant; the flag is
off by default and adopted deliberately per host.

The tail check earns its place from a real outage. On managed
PostgreSQL the platform's hidden sessions make the audit tail's resume
horizon uncomputable unless the writing roles are asserted
(`GLASSHOUSE_AUDIT_WRITER_ROLES`); misconfigured, everything that reads
the ledger refuses - the projector, `seed`, `verify` - while the service
itself comes up healthy and serves stale reads until someone notices.
Reading one tail here turns that into a FAILED DEPLOY carrying the
substrate's own remedy, which is where a configuration error belongs.
The deliberate trade: a transient refusal now blocks a deploy rather
than degrading a running demo. That is the right way round - the deploy
is watched, 02:30 is not.

This module never prints; the CLI renders the report and turns
`ProvisionError` into stderr plus exit 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from glasshouse.commit import MODEL_FILE, GlasshouseClient, apply_views
from glasshouse.commit.morpholog_client.envelopes import LeastPrivilege
from glasshouse.compute.store import engine_url

# The migration bundle. Resolved as a constant so the preflight (and its
# tests, and seed's reset path) name one place.
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


class ProvisionError(Exception):
    """Provisioning cannot proceed (no migration bundle to run)."""


def alembic_config(database_url: str, *, ini: Path = ALEMBIC_INI) -> Config:
    """The Alembic configuration, refused loudly when the bundle is
    absent: provisioning runs from the source checkout or the Docker
    image, not a wheel-only install."""
    if not ini.exists():
        raise ProvisionError(
            f"alembic.ini not found at {ini}; provisioning runs from the source "
            "checkout or the Docker image, not a wheel-only install"
        )
    config = Config(str(ini))
    config.set_main_option("sqlalchemy.url", engine_url(database_url))
    return config


@dataclass(frozen=True)
class ProvisionReport:
    governed: str  # the InitReport status: "initialised" | "already-initialised"
    least_privilege: LeastPrivilege | None
    # The governed-schema migrations this deploy applied. Empty on a fresh
    # database (init provisions at the binary's own version) and on one
    # already current, which is the ordinary case.
    governed_migrations: tuple[str, ...] = ()

    def render(self) -> str:
        applied = (
            f"governed schema {self.governed}"
            if not self.governed_migrations
            else f"governed schema {self.governed}, "
            f"migrated ({', '.join(self.governed_migrations)})"
        )
        lines = [f"provisioned: app schema at head, {applied}, views applied"]
        if self.least_privilege is not None:
            floor = self.least_privilege
            lines.append(
                f"least-privilege floor: reader {floor.reader_role}, writer {floor.writer_role}"
            )
            lines.extend(f"  next: {step}" for step in floor.next_steps)
        return "\n".join(lines)


def run_provision(
    database_url: str,
    *,
    least_privilege: bool = False,
    writer_roles: list[str] | None = None,
) -> ProvisionReport:
    """The whole operation, in dependency order: app schema first (the
    migration owns the TimescaleDB extension), then the governed schema,
    then the inspection views over it, then one audit tail read to prove
    the ledger is readable with this deployment's configuration."""
    command.upgrade(alembic_config(database_url), "head")
    client = GlasshouseClient(str(MODEL_FILE), database_url, writer_roles=writer_roles)
    init_report = client.init(skip_if_exists=True, least_privilege=least_privilege)
    # `init` provisions but never alters, so a governed schema laid down by
    # an older binary stays behind it forever - and since v0.0.8 the binary
    # says so and refuses rather than working against a schema it does not
    # expect. That refusal belongs to the watched deploy, not to the first
    # workload that touches the ledger, so bring it up here. A fresh
    # database is already current (init wrote this binary's own schema) and
    # a current one is left alone, which keeps provision idempotent.
    migration = client.migrate()
    engine = sa.create_engine(engine_url(database_url))
    try:
        apply_views(engine)
    finally:
        engine.dispose()
    # An empty tail is a lawful answer, so this costs nothing on a fresh
    # database; what it refuses is a deployment that cannot read the
    # ledger at all. The substrate's message names the remedy, so it is
    # carried through rather than summarised.
    client.audit()
    return ProvisionReport(
        governed=init_report.status,
        least_privilege=init_report.least_privilege,
        governed_migrations=tuple(str(ref.version) for ref in migration.applied),
    )
