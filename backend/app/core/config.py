from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str

    # Optional for later phases (not required yet)
    SUPABASE_URL: str | None = None
    SUPABASE_ANON_KEY: str | None = None
    REPORTS_BUCKET: str = "lci-reports"

    # -------------------------
    # Email / SMTP
    # -------------------------
    SMTP_USER: str | None = None  # reports@pulselci.com — used for subscriber report emails
    SMTP_PASS: str | None = None

    # Outreach sender — craig@pulselci.com (separate Workspace account + app password)
    OUTREACH_SMTP_USER: str | None = None
    OUTREACH_SMTP_PASS: str | None = None

    # Outscraper — bulk review ingestion (replaces Google's 5-review cap)
    OUTSCRAPER_API_KEY: str | None = None

    # Google Places (Phase C4+)
    GOOGLE_PLACES_API_KEY: str | None = None
    GOOGLE_PLACES_FIELDS: str = "rating,user_ratings_total"

    # Hunter.io (optional — email lookup fallback for outreach)
    HUNTER_API_KEY: str | None = None

    # Apollo.io (optional — email lookup fallback, higher hit rate than Hunter)
    APOLLO_API_KEY: str | None = None

    # -------------------------
    # Stripe (Billing)
    # -------------------------
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_starter: str | None = None
    stripe_price_growth: str | None = None
    stripe_success_url: str | None = None
    stripe_cancel_url: str | None = None

    # Admin / cron protection
    ADMIN_API_KEY: str | None = None

    model_config = SettingsConfigDict(
        env_file=r"C:\Users\201397\local-competitor-intelligence\.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()