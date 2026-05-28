"""Central config: paths, .env loading, and tunable settings.

Import this from any module to get consistent paths and settings without
re-reading the environment everywhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
CREDENTIALS_DIR = ROOT / "credentials"
TRAINING_DIR = ROOT / "training_data" / "good"

# Load .env once, on import.
load_dotenv(ROOT / ".env")

for _d in (DATA_DIR, LOGS_DIR, CREDENTIALS_DIR):
    _d.mkdir(exist_ok=True)


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Secrets / external services
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
    scrapingbee_api_key: str = os.getenv("SCRAPINGBEE_API_KEY", "")

    # Queue backend
    store_backend: str = os.getenv("STORE_BACKEND", "local").strip().lower()
    google_sheets_credentials: str = os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS", "./credentials/gsheets.json"
    )
    google_sheet_id: str = os.getenv("GOOGLE_SHEET_ID", "")

    # Scoring
    outreach_score_threshold: float = _f("OUTREACH_SCORE_THRESHOLD", 0.5)

    # Outreach guardrails
    daily_send_cap: int = _i("DAILY_SEND_CAP", 20)
    send_min_gap_seconds: int = _i("SEND_MIN_GAP_SECONDS", 60)
    send_max_gap_seconds: int = _i("SEND_MAX_GAP_SECONDS", 180)
    send_hour_start: int = _i("SEND_HOUR_START", 10)
    send_hour_end: int = _i("SEND_HOUR_END", 18)

    # Branding for templates
    business_name: str = os.getenv("BUSINESS_NAME", "SA Real Estate Media")
    sender_first_name: str = os.getenv("SENDER_FIRST_NAME", "Joel")


settings = Settings()
