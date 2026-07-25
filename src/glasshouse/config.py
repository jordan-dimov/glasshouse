"""Application settings.

All configuration comes from the environment (twelve-factor); nothing is
read from files at runtime. `GLASSHOUSE_MORPHOLOG_BIN` points at the
morpholog binary in development (the commit zone's `GlasshouseClient`
honours the same name); in the Docker image the binary is baked in at a
known path.
"""

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    # The shared demo login (HTTP Basic, username "demo"). None = no
    # gate: local dev and the pure tests run open. Set = everything but
    # the deployment probes requires it, and in the demo environment
    # browser writes refuse to run without it.
    demo_password: str | None = None
    # The organisation an authenticated write is bound to: with the demo
    # login active, org never comes from a request body.
    demo_org: str = "acme-energy"
    environment: Environment = "dev"

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
