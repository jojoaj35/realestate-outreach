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
EXPORTS_DIR = ROOT / "exports"
TRAINING_DIR = ROOT / "training_data" / "good"

# Load .env once, on import.
load_dotenv(ROOT / ".env")

for _d in (DATA_DIR, LOGS_DIR, CREDENTIALS_DIR, EXPORTS_DIR):
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


def _b(name: str, default: bool) -> bool:
    val = os.getenv(name, "").strip().lower()
    if val in {"1", "true", "yes", "y", "on"}:
        return True
    if val in {"0", "false", "no", "n", "off"}:
        return False
    return default


@dataclass
class Settings:
    # Secrets / external services
    scrapingbee_api_key: str = os.getenv("SCRAPINGBEE_API_KEY", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    hasdata_api_key: str = os.getenv("HASDATA_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    # Queue backend
    store_backend: str = os.getenv("STORE_BACKEND", "local").strip().lower()
    google_sheets_credentials: str = os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS", "./credentials/gsheets.json"
    )
    google_sheet_id: str = os.getenv("GOOGLE_SHEET_ID", "")

    # Scoring
    outreach_score_threshold: float = _f("OUTREACH_SCORE_THRESHOLD", 0.70)
    # Drone/aerial handling: skip listings that already use aerial photography.
    exclude_drone_listings: bool = _b("EXCLUDE_DRONE_LISTINGS", True)
    aerial_threshold: float = _f("AERIAL_THRESHOLD", 0.6)
    exclude_virtual_tour_listings: bool = _b("EXCLUDE_VIRTUAL_TOUR_LISTINGS", True)

    # Outreach guardrails
    daily_send_cap: int = _i("DAILY_SEND_CAP", 5)
    send_min_gap_seconds: int = _i("SEND_MIN_GAP_SECONDS", 75)
    send_max_gap_seconds: int = _i("SEND_MAX_GAP_SECONDS", 105)
    send_hour_start: int = _i("SEND_HOUR_START", 10)
    send_hour_end: int = _i("SEND_HOUR_END", 18)
    # Seconds to wait for an iMessage delivery receipt (read back from chat.db)
    # before marking a number undeliverable and skipping it. iMessage receipts
    # usually arrive within ~10-20s. Reading chat.db needs Full Disk Access.
    delivery_check_seconds: int = _i("DELIVERY_CHECK_SECONDS", 15)

    # Branding / message content for templates
    business_name: str = os.getenv("BUSINESS_NAME", "SA Real Estate Media")
    sender_first_name: str = os.getenv("SENDER_FIRST_NAME", "Joel")
    service_area: str = os.getenv("SERVICE_AREA", "San Antonio/Austin area")
    contact_phone: str = os.getenv("CONTACT_PHONE", "(210) 741-1810")
    website: str = os.getenv("WEBSITE", "")
    special_offer: str = os.getenv(
        "SPECIAL_OFFER", "$99 for 25 photos + 5 drone photos"
    )
    include_opt_out: bool = _b("INCLUDE_OPT_OUT", True)

    # Instagram outreach
    ig_daily_dm_cap: int = _i("IG_DAILY_DM_CAP", 10)
    ig_min_gap_seconds: int = _i("IG_MIN_GAP_SECONDS", 120)
    ig_max_gap_seconds: int = _i("IG_MAX_GAP_SECONDS", 300)
    ig_send_mode: str = os.getenv("IG_SEND_MODE", "assisted").strip().lower()
    ig_browser_profile: str = os.getenv("IG_BROWSER_PROFILE", "./browser_profile/instagram")
    ig_discovery_hashtags: str = os.getenv(
        "IG_DISCOVERY_HASHTAGS",
        "sanantoniorealtor,satxrealtor,austinrealtor,atxrealtor",
    )
    ig_exclude_keywords: str = os.getenv(
        "IG_EXCLUDE_KEYWORDS",
        "lender,mortgage,loan officer,photographer,staging,stager,title company,insurance",
    )
    ig_realtor_keywords: str = os.getenv(
        "IG_REALTOR_KEYWORDS",
        "realtor,real estate,realty,broker,agent,listing,properties,homes",
    )
    ig_skip_already_following: bool = _b("IG_SKIP_ALREADY_FOLLOWING", True)
    # CLIP ranking thresholds (see ig_rank.py)
    ig_realtor_score_threshold: float = _f("IG_REALTOR_SCORE_THRESHOLD", 0.55)
    ig_city_score_threshold: float = _f("IG_CITY_SCORE_THRESHOLD", 0.45)
    ig_exclude_score_threshold: float = _f("IG_EXCLUDE_SCORE_THRESHOLD", 0.60)
    ig_semantic_dup_threshold: float = _f("IG_SEMANTIC_DUP_THRESHOLD", 0.92)
    ig_google_search_enabled: bool = _b("IG_GOOGLE_SEARCH_ENABLED", True)
    # OpenAI — AI discovery agent (default) or CLIP hybrid rerank
    ig_llm_enabled: bool = _b("IG_LLM_ENABLED", True)
    ig_llm_mode: str = os.getenv("IG_LLM_MODE", "agent").strip().lower()
    ig_llm_model: str = os.getenv("IG_LLM_MODEL", "gpt-4o-mini")
    ig_llm_expand_queries: bool = _b("IG_LLM_EXPAND_QUERIES", True)
    ig_llm_auto_reject_match: float = _f("IG_LLM_AUTO_REJECT_MATCH", 0.40)
    ig_llm_auto_reject_exclude: float = _f("IG_LLM_AUTO_REJECT_EXCLUDE", 0.55)
    ig_llm_auto_accept_match: float = _f("IG_LLM_AUTO_ACCEPT_MATCH", 0.72)
    ig_llm_auto_accept_city: float = _f("IG_LLM_AUTO_ACCEPT_CITY", 0.52)
    ig_llm_auto_accept_exclude: float = _f("IG_LLM_AUTO_ACCEPT_EXCLUDE", 0.45)


settings = Settings()
