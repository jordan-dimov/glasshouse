"""Application settings.

All configuration comes from the environment (twelve-factor); nothing is
read from files at runtime. `GLASSHOUSE_MORPHOLOG_BIN` points at the
morpholog binary in development (the commit zone's `GlasshouseClient`
honours the same name); in the Docker image the binary is baked in at a
known path.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# dev is local; demo and production are hosted (Render), where logs are
# operational records and render as JSON lines. An unknown value is
# refused at settings construction rather than silently treated as dev.
Environment = Literal["dev", "demo", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GLASSHOUSE_")

    database_url: str = "postgresql://glasshouse:glasshouse@localhost:5432/glasshouse"
    morpholog_bin: str = "morpholog"
    morpholog_timeout_seconds: float = 10.0  # API-boundary operations; imports run unbounded
    environment: Environment = "dev"


def get_settings() -> Settings:
    return Settings()
