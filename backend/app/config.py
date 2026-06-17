from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / .env file."""

    # 127.0.0.1 (not localhost): on Windows localhost may resolve to IPv6 ::1
    # first, where an unrelated process can be listening on 5432.
    database_url: str = (
        "postgresql+psycopg2://csv2db:csv2db@127.0.0.1:5432/csv2db"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
