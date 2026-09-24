"""Application settings, loaded once from environment variables.

Locally, values come from `.env` (override=True so a stale shell variable can
never shadow the file). On Vercel there is no .env file; values come from the
project's Environment Variables. Tests set APP_ENV=test to skip .env entirely.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

if os.getenv("APP_ENV") != "test":
    load_dotenv(BASE_DIR / ".env", override=True)


def normalize_database_url(url: str) -> str:
    """Neon hands out `postgresql://...`; SQLAlchemy needs the psycopg 3 driver
    named explicitly. Accept both forms so a pasted Neon URL just works."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings:
    def __init__(self) -> None:
        raw_url = os.getenv("DATABASE_URL", "").strip()
        if not raw_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
            )
        self.database_url = normalize_database_url(raw_url)
        # Shared secret Vapi sends in the `x-vapi-secret` header on every webhook.
        self.vapi_webhook_secret = os.getenv("VAPI_WEBHOOK_SECRET", "").strip()
        # Mock appointment slots are generated in the clinic's local time.
        self.clinic_timezone = os.getenv("CLINIC_TIMEZONE", "America/New_York")
        # Shown on the dashboard so reviewers know which number to call.
        self.intake_phone_number = os.getenv("INTAKE_PHONE_NUMBER", "+17579798389")


settings = Settings()
