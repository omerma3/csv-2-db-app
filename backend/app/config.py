from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / .env file."""

    # localhost works because docker-compose publishes Postgres on IPv4 loopback
    # only (127.0.0.1:5432), so the IPv6 (::1) attempt is refused and the client
    # falls back to IPv4 — avoiding Docker Desktop's broken IPv6-loopback path.
    database_url: str = (
        "postgresql+psycopg2://csv2db:csv2db@localhost:5432/csv2db"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
