#!/usr/bin/env bash
# Start the Mac-side scraper API that the Render dashboard calls for Zillow
# scraping (see scraper_api.py). Keep this running, plus ngrok:
#   ngrok http --domain=<your-static-domain> 8765
set -euo pipefail
cd "$(dirname "$0")/.."

PY=./venv/bin/python
$PY -c "import fastapi, uvicorn" 2>/dev/null || ./venv/bin/pip install fastapi "uvicorn[standard]"

echo "Scraper API → http://127.0.0.1:8765  (Render reaches it via your ngrok URL)"
exec $PY -m uvicorn scraper_api:app --host 127.0.0.1 --port 8765
