from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str

    # Optional for later phases (not required yet)
    SUPABASE_URL: str | None = None
    SUPABASE_ANON_KEY: str | None = None
    REPORTS_BUCKET: str = "lci-reports"

    # Google Places (Phase C4+)
    GOOGLE_PLACES_API_KEY: str | None = None
    GOOGLE_PLACES_FIELDS: str = "rating,user_ratings_total"

    model_config = SettingsConfigDict(
        env_file=r"C:\Users\201397\local-competitor-intelligence\.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
