"""`glasshouse provision`: non-destructive first-boot provisioning.

The three idempotent steps a fresh (or already-provisioned) database
needs before the web service can serve: migrate the app schema to head,
initialise the governed schema if absent (`init --skip-if-exists`), and
apply the sealed inspection views. This is the web service's pre-deploy
command (DESIGN section 13: migrations and the Morpholog bootstrap run
in a pre-deploy step, never at app startup) and is safe to run on every
deploy: the migration no-ops at head, the init skips an existing schema,
and the committed view script re-applies and re-seals.

`--least-privilege` passes the upstream provisioning floor through
(reader/writer group roles; the report names the membership grants only
the operator can decide - printed, never executed). It needs CREATEROLE
on the connecting role, which managed hosts may not grant; the flag is
off by default and adopted deliberately per host.

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

    def render(self) -> str:
        lines = [f"provisioned: app schema at head, governed schema {self.governed}, views applied"]
        if self.least_privilege is not None:
            floor = self.least_privilege
            lines.append(
                f"least-privilege floor: reader {floor.reader_role}, writer {floor.writer_role}"
            )
            lines.extend(f"  next: {step}" for step in floor.next_steps)
        return "\n".join(lines)


def run_provision(database_url: str, *, least_privilege: bool = False) -> ProvisionReport:
    """The whole operation, in dependency order: app schema first (the
    migration owns the TimescaleDB extension), then the governed schema,
    then the inspection views over it."""
    command.upgrade(alembic_config(database_url), "head")
    client = GlasshouseClient(str(MODEL_FILE), database_url)
    init_report = client.init(skip_if_exists=True, least_privilege=least_privilege)
    engine = sa.create_engine(engine_url(database_url))
    try:
        apply_views(engine)
    finally:
        engine.dispose()
    return ProvisionReport(governed=init_report.status, least_privilege=init_report.least_privilege)
