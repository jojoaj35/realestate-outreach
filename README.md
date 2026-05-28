# Real Estate Photography Outreach

AI-assisted outreach for a real estate photography business. It scrapes
listings, scores their photos against your professional style, queues the weak
ones, and sends personalized cold iMessages to the listing agent — with
TCPA/Apple-ID guardrails. Replies are auto-classified and STOP requests are
honored automatically.

Full plan: [`../realestate-outreach-prd.md`](../realestate-outreach-prd.md) on Desktop.

## Pipeline

```
scrape ──► score ──► enqueue ──► outreach ──► replies
(realtor) (photos) (queue)    (iMessage)   (classify + DNC)
```

One entry point wires it all together:

```bash
python run.py --help          # see all stages
python run.py status          # queue summary
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium    # one-time, for the scraper
cp .env.example .env           # then fill in keys/settings
```

Everything works out of the box with the **local CSV store** (`data/queue.csv`).
To use Google Sheets instead, set `STORE_BACKEND=sheets` plus
`GOOGLE_SHEETS_CREDENTIALS` / `GOOGLE_SHEET_ID` in `.env`.

## Train / tune the photo scorer (already done once)

```bash
bash scripts/download_training_data.sh   # pull good photos from Drive
python src/calibrate.py                  # heuristic thresholds from good set
python src/train_clip.py                 # CLIP centroid of your style
```

Outputs under `models/`: `good_centroid.npy`, `good_embeddings.npy`,
`thresholds.json`. Scoring is **one-class anomaly detection** — "how unlike my
pro photos is this?" — so no bad-photo training set is needed.

## Daily use

```bash
# 1. Scrape + score + queue (opens a real Chrome; solve any CAPTCHA once)
python run.py pipeline "https://www.realtor.com/realestateandhomes-search/San-Antonio_TX" --pages 2

# 2. Review what's queued
python run.py status

# 3. Send — DRY RUN first (no texts sent), then for real
python run.py outreach --max 5
python run.py outreach --max 5 --send

# 4. Pull in replies (classify + auto-DNC on STOP)
python run.py replies
```

Score a single image (debugging): `python src/score.py path/to/photo.jpg`

## Automation (launchd)

Local scheduling fits because iMessage and `chat.db` live on this Mac:

```bash
bash scripts/install_launchd.sh            # replies every 15m + outreach 10/12/14/16
bash scripts/install_launchd.sh uninstall  # stop everything
```

Grant **Full Disk Access** to your shell so the replies poller can read
`chat.db` (System Settings → Privacy & Security → Full Disk Access). Scraping
stays manual since it may need you to clear a browser challenge.

## Guardrails (enforced in `src/outreach.py`)

- Do-not-contact list checked before every send; STOP replies auto-added.
- Daily cap per Apple ID (`DAILY_SEND_CAP`), counted from the store.
- Business-hours window (`SEND_HOUR_START`/`END`).
- iMessage-only: sends bind to the iMessage service, so undeliverable numbers
  error instead of silently going green-bubble SMS.
- Random pacing between sends; opt-out line in every template.

## Layout

| path | purpose |
|------|---------|
| `run.py` | orchestrator CLI (all stages) |
| `src/scrape_realtor.py` | realtor.com scraper (Playwright) |
| `src/photos.py` | download + upscale listing photos |
| `src/heuristics.py` `src/score.py` `src/score_listings.py` | photo scoring |
| `src/store.py` | queue store (local CSV or Google Sheets) |
| `src/templates.py` `src/imessage.py` `src/outreach.py` | outreach |
| `src/reply_poller.py` | reply classification + DNC |
| `src/config.py` | paths + settings from `.env` |
| `scripts/` | training download + launchd installer |
| `tests/` | `pytest` suite (no GPU/network/Mac needed) |

## Tests

```bash
pytest -q
```

## Scoring state (2026-05-23)

- Trained on 58 good photos. Centroid: mean cosine 0.873, min 0.743.
- Heuristic thresholds auto-calibrated to the 5th percentile of the good set.
- Next tuning step: hand-label ~30 bad MLS photos to lock the outreach threshold
  for precision (better to miss a bad listing than spam a good one).
