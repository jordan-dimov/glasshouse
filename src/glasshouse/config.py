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
    # The agent's way in over HTTP (`glasshouse.agents`, served at /mcp).
    # None = not served at all. Set = a caller presenting this bearer
    # token acts as `mcp_actor` in `demo_org`, with whatever authority
    # the ledger has granted that actor and no more.
    mcp_token: str | None = None
    mcp_actor: str = "agent"

    @field_validator("mcp_token", mode="before")
    @classmethod
    def _a_blank_token_is_no_token(cls, value: object) -> object:
        # A variable left empty in a hosting dashboard means "not set":
        # the door stays shut, which is the safe reading. (A blank demo
        # password is different, and refused below: that one would open
        # something.)
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("demo_password", "mcp_token")
    @classmethod
    def _password_is_a_real_perimeter(cls, value: str | None) -> str | None:
        # Each secret is the whole perimeter of what it guards on a
        # public deployment: a blank or trivial value must fail LOUDLY at
        # boot, never quietly enable the gate (or lift the write fence,
        # or open the agent's door) with an empty one.
        if value is None:
            return None
        if len(value.strip()) < 12:
            raise ValueError(
                "GLASSHOUSE_DEMO_PASSWORD and GLASSHOUSE_MCP_TOKEN must each be at "
                "least 12 non-blank characters (each is the entire perimeter of what "
                "it guards); unset one to switch its door off"
            )
        return value


def get_settings() -> Settings:
    return Settings()
