"""Application settings.

All configuration comes from the environment (twelve-factor); nothing is
read from files at runtime. `GLASSHOUSE_MORPHOLOG_BIN` points at the
morpholog binary in development (the commit zone's `GlasshouseClient`
honours the same name); in the Docker image the binary is baked in at a
known path.
"""

import json
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# dev is local; demo and production are hosted (Render), where logs are
# operational records and render as JSON lines. An unknown value is
# refused at settings construction rather than silently treated as dev.
Environment = Literal["dev", "demo", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GLASSHOUSE_")

    database_url: str = "postgresql://glasshouse:glasshouse@localhost:5432/glasshouse"
    morpholog_bin: str = "morpholog"
    # Bounds API-boundary operations; CLI imports run unbounded.
    morpholog_timeout_seconds: float = 10.0
    # The session roles that write morpholog.audit, asserted so the audit
    # tail's resume horizon can be computed over their sessions alone.
    # Empty on a self-hosted database, where the horizon reads every
    # session and is sound without help. Managed PostgreSQL (Render's
    # included) hides the platform's own sessions from the application
    # role and grants no pg_read_all_stats, so the default horizon is
    # structurally uncomputable and the tail refuses rather than skip a
    # row; naming the one role every writer connects as restores it.
    # Comma-separated or JSON, e.g. GLASSHOUSE_AUDIT_WRITER_ROLES=app_user.
    audit_writer_roles: Annotated[list[str], NoDecode] = []
    # The shared demo login (HTTP Basic, username "demo"). None = no
    # gate: local dev and the pure tests run open. Set = everything but
    # the deployment probes requires it, and in the demo environment
    # browser writes refuse to run without it.
    demo_password: str | None = None
    # The organisation an authenticated write is bound to: with the demo
    # login active, org never comes from a request body.
    demo_org: str = "acme-energy"
    environment: Environment = "dev"

    @field_validator("audit_writer_roles", mode="before")
    @classmethod
    def _split_roles(cls, value: object) -> object:
        # NoDecode above keeps the environment source from JSON-decoding
        # the value, so a comma-separated list parses here: an operator
        # naming one role in a hosting dashboard should not have to
        # spell it `["role"]`, and JSON still works for anyone who does.
        # Both spellings normalise identically - a stray space around a
        # role name would otherwise survive JSON and reach the substrate
        # as a role that does not exist, refused at the first audit tail
        # rather than at boot. Anything that is not a list of strings is
        # left for the field's own validation to reject.
        if isinstance(value, str):
            text = value.strip()
            value = json.loads(text) if text.startswith("[") else text.split(",")
        if isinstance(value, list):
            named = [part.strip() if isinstance(part, str) else part for part in value]
            return [part for part in named if part != ""]
        return value

    @field_validator("demo_password")
    @classmethod
    def _password_is_a_real_perimeter(cls, value: str | None) -> str | None:
        # This one secret is the whole perimeter of a public deployment:
        # a blank or trivial value must fail LOUDLY at boot, never
        # quietly enable the gate (or lift the write fence) with an
        # empty password.
        if value is None:
            return None
        if len(value.strip()) < 12:
            raise ValueError(
                "GLASSHOUSE_DEMO_PASSWORD must be at least 12 non-blank characters "
                "(it is the entire perimeter of a public deployment); unset it to "
                "run without the gate"
            )
        return value


def get_settings() -> Settings:
    return Settings()
