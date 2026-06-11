"""Production entry point for Render (or any WSGI host).

Start command:  gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 300

Single worker is required: the dashboard keeps job state in memory.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "web"))

from app import app  # noqa: E402,F401
