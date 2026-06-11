# Real Estate Photography Outreach

AI-assisted outreach for a real estate photography business. It scrapes
listings, scores their photos against your professional style, queues the weak
ones, and sends personalized cold iMessages to the listing agent — with
TCPA/Apple-ID guardrails. This is **send-only**: it does not read or reply to
incoming messages. Honor opt-outs with `python run.py dnc <number>`.

Full plan: [`../realestate-outreach-prd.md`](../realestate-outreach-prd.md) on Desktop.

**Agent handoff (architecture, modules, session history):** [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md)

## Pipeline

```
scrape ──► score ──► enqueue ──► outreach
(realtor) (photos) (queue)    (iMessage, send-only)
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

# If anyone asks to stop, record it so they're never texted again:
python run.py dnc +12105550123 "asked to stop"
```

## Instagram outreach (Tool 2)

Find realtors on Instagram by city and DM them your intro offer. Uses a
**separate queue** (`data/ig_queue.csv`) from the listing pipeline.

**Assisted mode (default):** opens each profile and copies the message to your
clipboard — you paste and send in Instagram. **Automated mode** uses Playwright
to click Message and send (higher ban risk; keep volume low).

Log into Instagram once in the browser window; session persists under
`browser_profile/instagram/`.

```bash
python run.py ig-discover --city "San Antonio" --max 30
python run.py ig-status
python run.py ig-dm --max 5              # dry run
python run.py ig-dm --max 5 --send       # assisted: open profile + clipboard
python run.py ig-dm --max 3 --send --automated   # Playwright auto-send
python run.py ig-dnc @somehandle "asked to stop"
```

The web dashboard has an **Instagram DMs** tab. Configure caps in `.env`
(`IG_DAILY_DM_CAP`, `IG_DISCOVERY_HASHTAGS`, etc.).

Score a single image (debugging): `python src/score.py path/to/photo.jpg`

## Automation (launchd)

Local scheduling fits because iMessage lives on this Mac:

```bash
bash scripts/install_launchd.sh            # outreach batches at 10/12/14/16
bash scripts/install_launchd.sh uninstall  # stop sends
```

Scraping stays manual since it may need you to clear a browser challenge.

## Guardrails (enforced in `src/outreach.py`)

- Do-not-contact list checked before every send (add opt-outs with `run.py dnc`).
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
| `src/templates.py` `src/imessage.py` `src/outreach.py` | iMessage outreach (send-only) |
| `src/ig_discover.py` `src/ig_dm.py` `src/ig_store.py` `src/ig_browser.py` | Instagram discover + DM outreach |
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
