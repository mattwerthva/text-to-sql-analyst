"""Application settings, loaded from environment / .env.

Uses pydantic-settings so config is typed and validated at startup.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "text-to-sql-analyst"
    environment: str = "development"

    # Comma-separated in the env var; parsed into a list below.
    cors_origins: str = "http://localhost:5173"

    # --- Anthropic ---
    # Loaded from ANTHROPIC_API_KEY in .env. Required for /api/ask.
    anthropic_api_key: str = ""
    # Swap to a cheaper model (e.g. claude-haiku-4-5) while iterating.
    anthropic_model: str = "claude-opus-4-8"

    # --- Database ---
    database_url: str = "postgresql://dev:devpass@localhost:5432/sandbox"

    # --- Guardrail knobs (see app/db.py) ---
    # Every generated query is capped at this many rows.
    sql_row_limit: int = 500
    # Postgres statement_timeout for each query, in milliseconds.
    sql_statement_timeout_ms: int = 5000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
