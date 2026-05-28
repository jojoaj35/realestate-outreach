#!/bin/bash
# Send a small batch of cold iMessages. Run by launchd a few times during
# business hours. Keeps the Mac awake for the duration with caffeinate.
#
# REAL SENDS: this passes --send. The Python layer still enforces the daily
# cap, DNC list, business-hours window, pacing, and iMessage-only checks.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs

BATCH="${OUTREACH_BATCH:-5}"
exec caffeinate -i venv/bin/python run.py outreach --send --max "$BATCH" >> logs/outreach.log 2>&1
