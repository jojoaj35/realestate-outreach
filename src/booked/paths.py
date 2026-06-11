"""Default paths for the booked-agent pipeline.

External data lives outside the repo (Downloads / Claude session outputs), so
the source paths are configurable via environment variables and fall back to
the known locations confirmed during planning. Intermediate + output artifacts
are written under the repo's ``data/`` and ``exports/`` directories.
"""
from __future__ import annotations

import os
from pathlib import Path

from config import DATA_DIR, EXPORTS_DIR, MODELS_DIR, ROOT

# ── External source data (override via env) ─────────────────────────────────
IG_EXPORT_DIR = Path(
    os.getenv(
        "BOOKED_IG_EXPORT_DIR",
        str(Path.home() / "Downloads" / "instagram-sarealestatemedia-2026-06-03-oWaNBAHr"),
    )
)
IG_MESSAGES_DIR = IG_EXPORT_DIR / "your_instagram_activity" / "messages"

IMESSAGE_CSV = Path(
    os.getenv(
        "BOOKED_IMESSAGE_CSV",
        "/Users/joelwilson/Library/Application Support/Claude/local-agent-mode-sessions/"
        "f106f689-9dfd-4ef6-bf5d-77a74c516ea3/e68150be-24ff-4122-86d1-1e13bee25d08/"
        "local_987d0b9d-2dae-45f1-811c-c62f1220a6ac/outputs/iMessage_outreach_by_contact.csv",
    )
)

SQUARE_CSV = Path(
    os.getenv(
        "BOOKED_SQUARE_CSV",
        str(Path.home() / "Downloads" / "invoices-export-20260603T1642.csv"),
    )
)
CASHAPP_CSV = Path(
    os.getenv(
        "BOOKED_CASHAPP_CSV",
        str(Path.home() / "Downloads" / "cash_app_report_1780505311720.csv"),
    )
)

# ── Intermediate + output artifacts (inside repo) ───────────────────────────
BOOKED_DIR = DATA_DIR / "booked"
BOOKED_DIR.mkdir(parents=True, exist_ok=True)

PAYMENTS_CSV = BOOKED_DIR / "payments.csv"
IG_THREADS_JSONL = BOOKED_DIR / "ig_threads.jsonl"
IMSG_THREADS_JSONL = BOOKED_DIR / "imsg_threads.jsonl"
IDENTITIES_CSV = BOOKED_DIR / "identities.csv"
LABELED_CSV = BOOKED_DIR / "labeled_contacts.csv"
FEATURES_CSV = BOOKED_DIR / "features.csv"

BOOKED_EXPORT_CSV = EXPORTS_DIR / "booked_contacts.csv"
MODEL_PATH = MODELS_DIR / "booked_propensity.joblib"
MODEL_REPORT = ROOT / "BOOKED_MODEL_REPORT.md"
