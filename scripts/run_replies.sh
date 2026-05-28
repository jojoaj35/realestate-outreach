#!/bin/bash
# Poll Messages for replies, classify, enforce DNC. Run by launchd every ~15 min.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec venv/bin/python run.py replies >> logs/replies.log 2>&1
