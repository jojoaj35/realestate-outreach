# Project Handoff — Real Estate Photography Outreach (Zillow Edition)

**Read this first** when continuing work in a new chat or with a different model.

| Item | Path / Value |
|------|--------------|
| **Project root** | `/Users/joelwilson/Desktop/realestate-outreach/` |
| **Entry point (CLI)** | `run.py` |
| **Config** | `.env` (copy from `.env.example`) |
| **Python** | `./venv/bin/python` (project virtualenv) |
| **Web dashboard** | `python run.py web --port 5001` → http://127.0.0.1:5001 |
| **Listing source** | **Zillow only** (Scrapling stealth browser) |
| **Last updated** | June 9, 2026 |

---

## 1. What this project does

macOS-local outreach tool for **SA Real Estate Media** (Joel). It:

1. Scrapes **active Zillow listings** for any city (or a pasted Zillow link).
2. Scores listing photos with a CLIP model to find **non-professional / phone-quality** listings (the outreach opportunity) while keeping **professional** ones — including **twilight/dusk** shots — out of the queue.
3. Filters out **drone/aerial** and **virtual-tour** listings (those sellers already hired a pro).
4. Queues the listing **agents** with weak photos and sends **personalized cold iMessages** via the Messages app.
5. Optionally runs a **second pipeline** for **Instagram DM** outreach to realtors (separate queue, unchanged this session).

**Send-only:** no reply reading, no auto-replies. Opt-outs via `python run.py dnc <phone>`.

---

## 2. Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│  run.py (CLI)              src/web/app.py (Flask dashboard)       │
└────────────┬───────────────────────────────┬────────────────────┘
             │                               │
   ┌─────────▼─────────┐           ┌─────────▼─────────┐
   │  Zillow / iMessage│           │  Instagram        │
   │  pipeline         │           │  pipeline         │
   └─────────┬─────────┘           └─────────┬─────────┘
             │                               │
   scrape_zillow ──► scan ──► store        ig_discover ──► ig_store
        │                │     (queue.csv)        │             │
        ▼                ▼                        ▼             ▼
   score_listings ◄── score.py + heuristics    ig_rank/llm   ig_dm
        │                │                        │
        ▼                ▼                        │
   outreach ──► imessage + templates.py     ig_browser (Playwright)
```

---

## 3. The Zillow pipeline (primary tool)

### 3.1 Goal
Find listings with **bad photos** in any city and text the **listing agent** an offer for professional photography.

### 3.2 Main workflow
```bash
cd /Users/joelwilson/Desktop/realestate-outreach
./venv/bin/python run.py scan --city "San Antonio" --state TX --count 40 --keep 5
./venv/bin/python run.py scan --url "https://www.zillow.com/homes/Austin,-TX_rb/"
./venv/bin/python run.py status
./venv/bin/python run.py outreach --max 5                 # dry run
./venv/bin/python run.py outreach --max 5 --send --force  # real send
./venv/bin/python run.py web --port 5001                  # dashboard
```

### 3.3 Two-phase scan (`src/scan.py`)
The scan is intentionally two-phase for speed and cost:

1. **Phase 1 — discover (cheap):** fetch the Zillow search page (or pasted link) and parse ~40 result cards. Each card already carries a **hero photo + price + address + beds/baths**. Each hero is CLIP-scored (`_hero_clip`); we keep the **worst `keep`** without opening any detail page.
2. **Phase 2 — enrich (only the kept ones):** open each kept listing's `/homedetails/` page with the stealth browser to read the **listing-agent name/phone/brokerage** and the **full photo gallery**, then full-score the gallery. Agent contact never appears on search cards — you must "click into" each listing.

Skips: **virtual-tour** listings (Phase 1) and **drone** listings (Phase 2). Only listings with a **valid listing-agent name** are stored.

`scan()` signature:
```python
scan(city="Austin", count=200, keep=15, max_pro_score=None,
     state="TX", url=None, progress=None)
```
`url` (a Zillow search or single-listing link) **overrides** city/state when provided.

### 3.4 Zillow scraper (`src/scrape_zillow.py`)
Ported from the standalone `~/Desktop/zillow-scraper`. Key points:

- **Engine:** Scrapling `StealthySession` (`headless=True, humanize=True, block_images=True, block_webrtc=True, solve_cloudflare=False`) — gets past Zillow's PerimeterX/reCAPTCHA bot wall. No API credits.
- **Search parsing:** pulls listings from Zillow's embedded `__NEXT_DATA__` JSON blob (`listResults`), with an inline-`application/json` fallback. The pure parsers use stdlib regex to extract the script tags, so they are dependency-free and unit-testable.
- **Detail parsing:** re-parses `props.pageProps.componentProps.gdpClientCache` (itself a stringified JSON graph) and walks it for the agent attribution keys (`agentName`, `agentPhoneNumber`, `brokerName`, `brokerPhoneNumber`). Full gallery photos are scraped from `photos.zillowstatic.com/fp/...` and de-duplicated by photo hash (Zillow serves several sizes per photo).
- **Phone hygiene:** `_norm_phone` formats to `NNN-NNN-NNNN` and rejects toll-free prefixes. `valid_listing_agent` requires a real 2-token human name (rejects "Zillow", "Listing Agent", "Premier Agent").

Public functions (the pipeline interface, same shape the old scrapers had):

| Function | Purpose |
|----------|---------|
| `discover_listings(city, state, max_urls, session=None)` | Phase 1 from a city/state search |
| `discover_listings_from_url(url, max_urls, session=None)` | Phase 1 from a pasted link (search **or** single `/homedetails/`) |
| `enrich_agents(listings, city, state, session=None)` | Phase 2: detail pages → agent contact + full gallery (mutates in place) |
| `valid_listing_agent(name)` | True if a real person's name |
| `parse_search_cards(html)` / `parse_detail(html, listing)` | Pure HTML→`Listing` parsers (unit-tested) |
| `listing_from_raw(raw)` | Normalize one Zillow `listResults` entry → `Listing` |
| `build_search_url(city, state)` | e.g. `https://www.zillow.com/homes/Austin,-TX_rb/` |
| `scrape(city, state, max_results, enrich)` | Standalone convenience (discover + optional enrich) |

The shared `Listing` dataclass lives in `src/scrape_realtor.py` (kept only for that schema + the legacy realtor.com path).

### 3.5 Photo scoring

**`src/score.py` — CLIP classifier**
- Model: `openai/clip-vit-base-patch32` (PyTorch, MPS on Mac).
- `classify(path)` returns:
  - **`pro_prob`** (0–1): **k-NN in CLIP space** vs the labeled sets `models/good_embeddings.npy` (professional) and `models/target_embeddings.npy` (amateur). Higher = more professional.
  - **`aerial_prob`** (0–1): zero-shot text prompts (drone/aerial vs ground-level).
- k-NN params: `KNN_K=5`, `KNN_SCALE=20.0`.

**`src/heuristics.py` — OpenCV checks**
- Blur, exposure, orientation, and **`vertical_straightness(gray)`** (camera level → 1.0, tilted/keystone → 0.0).

**`src/score_listings.py` — listing-level score**
- Samples up to 10 photos. `pro_style_score` = trimmed mean of `pro_prob` (drops the worst 20% of photos so a few weak detail shots don't tank a pro listing); `vertical_score` = mean straightness.
- **`clip_score`** (the UI "pro" badge) = `0.80 × pro_style + 0.20 × vertical`. **Lower = worse photos = better outreach target.**
  - *Tuning history:* was `0.5 × pro_style + 0.5 × vertical`, but a diagnostic (`scripts/diagnose_model.py`) showed CLIP `pro_style` cleanly separates the classes (pro≈0.81 vs amateur≈0.17) while `vertical` does not (pro≈0.46 vs amateur≈0.40). The old 50/50 blend dragged genuine pro listings down to ~0.63 and even rescued some amateurs. Weights now live as named constants (`W_PRO_STYLE`, `W_VERTICAL`, `W_PROFESSIONAL`, `W_HEURISTIC`) at the top of `score_listings.py`.
- `has_drone` = any photo `aerial_prob >= AERIAL_THRESHOLD` (0.6).
- `score <= OUTREACH_SCORE_THRESHOLD` (0.5) → row stored as **`queued`**.
- **Diagnostic:** `./venv/bin/python scripts/diagnose_model.py` prints per-listing `pro_style`/`vertical`/`clip_score` for the labeled sets so you can see which signal is doing the work before retuning.

### 3.6 Training the model (`src/train_clip.py` + `scripts/add_training_listings.py`)

The classifier is **only as good as its labeled examples**. To teach it:

```bash
# Add a professional listing (incl. twilight/dusk) → good bucket:
./venv/bin/python scripts/add_training_listings.py good  "<zillow-url>" ["<url2>" ...]

# Add an amateur/bad listing → target bucket:
./venv/bin/python scripts/add_training_listings.py target "<zillow-url>"

# Retrain prototypes:
./venv/bin/python src/train_clip.py
```

`add_training_listings.py` fetches each listing's full gallery via the Zillow scraper and saves the photos under `training_data/<good|target>/<slug>_<zpid>/NN.jpg`. `train_clip.py` re-embeds both folders, writes `good_embeddings.npy` / `target_embeddings.npy` / `good_centroid.npy`, and prints a leave-one-out accuracy.

**Current training set:** `good` = **283 images**, `target` = **204 images**, **leave-one-out accuracy ≈ 97%**.

### 3.7 Store (`src/store.py`)
- Default: `LocalStore` → `data/queue.csv`. Optional `SheetStore` (`STORE_BACKEND=sheets`).
- Status lifecycle: `new` → `queued` → `sent` → (`replied`/`booked`) | `skipped` | `dnc`.
- `upsert_listings` refreshes score fields on existing rows but never clobbers outreach progress.

### 3.8 Outreach (`src/outreach.py`, `src/imessage.py`, `src/templates.py`)
- Guardrails: DNC, daily cap, business-hours window (10–18), pacing, duplicate-agent block, **iMessage-only**.
- `send_selected_detailed(ids, dry_run, force_hours)` powers the web send. **Dry run does not mutate the CSV.**
- Messages sent via AppleScript (`osascript`) → Messages app.

---

## 4. Web dashboard (`src/web/`)

**Start:** `python run.py web --port 5001`. **Restart required after code or template changes** — Flask runs with `debug=False`, so it caches templates in memory (this caused a "dashboard not updating" issue this session).

| API | Method | Purpose |
|-----|--------|---------|
| `/` | GET | Dashboard HTML |
| `/api/listings` | GET | All queue rows + status counts |
| `/api/scan` | POST | Start background scan → `{job_id}`. Body: `city, state, count, keep, max_pro_score, url` |
| `/api/scan/status` | GET | Poll scan progress |
| `/api/preview` | POST | Message preview for one listing |
| `/api/send` / `/api/send/status` | POST/GET | Background send + results |
| `/api/dnc` | POST | Add phone to DNC |
| `/api/export` / `/api/export/download` | POST/GET | Build + download `.xls` |
| `/api/ig/*` | — | Instagram routes (unchanged) |

**Scan form (this session):** the Source dropdown was **removed** (Zillow-only). Added a **"Zillow link (optional — overrides city/state)"** input that posts `url` to `/api/scan`.

---

## 5. CLI reference (`run.py`)

| Command | Notes |
|---------|-------|
| `scan` | Zillow-only. Flags: `--city --state --url --count --keep --max-pro-score` |
| `scrape` | Raw scrape → `listings.json`. `--source {realtor,zillow,hasdata}` (default `zillow`) |
| `pipeline` | scrape + score + enqueue. Same `--source` choices |
| `score` / `enqueue` | JSON scoring + load into store |
| `outreach` | Send iMessages to `queued` rows (`--send` to actually send, `--force` off-hours) |
| `dnc` / `status` / `export` | DNC add / queue summary / `.xls` export |
| `web` | Flask dashboard |
| `ig-*` | Instagram pipeline |

---

## 6. Folder layout

| Path | Purpose |
|------|---------|
| `run.py` | All CLI commands |
| `src/scrape_zillow.py` | **Zillow scraper (primary source)** |
| `src/scrape_hasdata.py` | HasData-API Zillow path (datacenter fallback) |
| `src/scrape_realtor.py` | `Listing` dataclass + legacy realtor.com path |
| `src/scan.py` | Two-phase Zillow scan |
| `src/score.py`, `heuristics.py`, `score_listings.py` | Photo scoring |
| `src/train_clip.py` | Build CLIP prototypes |
| `src/store.py` | Queue (`data/queue.csv`) |
| `src/outreach.py`, `imessage.py`, `templates.py`, `contacts.py` | Sending |
| `src/web/app.py`, `src/web/templates/index.html` | Dashboard |
| `src/ig_*.py` | Instagram pipeline |
| `scripts/add_training_listings.py` | **Download a Zillow listing's gallery into a training bucket** |
| `training_data/good/`, `training_data/target/` | Labeled photo sets |
| `models/` | CLIP embeddings + thresholds |
| `data/queue.csv`, `data/dnc.csv`, `data/photo_cache/` | Store + cache |
| `tests/` | pytest |
| `venv/` | Python virtualenv |

---

## 7. Configuration (`.env`)

```bash
STORE_BACKEND=local
OUTREACH_SCORE_THRESHOLD=0.5
EXCLUDE_DRONE_LISTINGS=true
EXCLUDE_VIRTUAL_TOUR_LISTINGS=true
AERIAL_THRESHOLD=0.6
DAILY_SEND_CAP=5
SEND_MIN_GAP_SECONDS=75
SEND_MAX_GAP_SECONDS=105
SEND_HOUR_START=10
SEND_HOUR_END=18
BUSINESS_NAME=SA Real Estate Media
SENDER_FIRST_NAME=Joel
SERVICE_AREA=San Antonio/Austin area
CONTACT_PHONE=(210) 741-1810
SPECIAL_OFFER=$99 for 25 photos + 5 drone photos
```
Note: `SCAN_SOURCE` was **removed** — the pipeline is Zillow-only.

---

## 8. Tests

```bash
./venv/bin/python -m pytest tests/test_zillow.py tests/test_hasdata.py -q
```
- `tests/test_zillow.py` — 8 dependency-free Zillow parser tests (search results, home-info/carousel fallback, agent attribution, gallery dedup, phone normalization, URL builder).
- Other suites (`test_store`, `test_templates`, `test_contacts`, `test_heuristics`, `test_ig`) require the full venv (`phonenumbers`, etc.) — run them with `./venv/bin/python`.

---

## 9. Session history (June 9, 2026)

1. **Ported the new Zillow scraper** into the pipeline as `src/scrape_zillow.py` (Scrapling + `__NEXT_DATA__` + `gdpClientCache` agent walk); added the two-phase interface and `tests/test_zillow.py`.
2. **Wired Zillow** into `scan.py`, `run.py`, `config.py`, and the web app/UI.
3. **Made the pipeline Zillow-only:** deleted `scrape_redfin.py`, `scrape_unlockmls.py`, `scrape_tavily.py` and their tests; rewrote `scan.py` Zillow-only; removed the obsolete `--source/--site/--max-age-days` flags and `scan_source` config.
4. **Added "paste a link" support:** `discover_listings_from_url()` + a Zillow link field in the dashboard.
5. **Fixed the dashboard not updating** — restarted the stale Flask server (it had cached the old template since June 6 because `debug=False`).
6. **Tuned the model for twilight:** added 6 professional listings (4 twilight, 225 photos) to `good`; retrained (98% LOO). Twilight now scores 0.82–0.89 (professional).
7. **Caught a missed bad listing:** added Oleander Chase (68 photos) to `target`; retrained (97% LOO). It now scores 0.19 (amateur).
8. **Built `scripts/add_training_listings.py`** as the reusable labeling tool.
9. **Cleared the queue:** backed up 124 rows to `data/queue_backup_20260609_103948.csv`, kept the 16 fresh (today's) listings.
10. **Verified end-to-end** with Austin and San Antonio scans (drone filtering working, twilight no longer flagged).

---

## 10. Known gotchas / open items

1. **Flask caches templates** (debug off) — **restart the server** after any code/template change.
2. **Cleared old queue rows included past `sent` history** (safe in the backup CSV). The active queue no longer "remembers" who was messaged; a re-scan could re-queue them. Merge the old `sent`/`dnc` rows back in if you want that history preserved.
3. **Zillow throttling:** detail pages occasionally hit 30s timeouts; the scraper retries automatically. A residential proxy + lower rate helps at volume.
4. **Model improves with labels** — feed more edge cases via `add_training_listings.py` (`good` = professional, `target` = amateur) and retrain.
5. **`clip_score` in the UI** is the style+vertical blend (now `0.80 × pro_style + 0.20 × vertical`, lower = worse photos = target), not raw CLIP alone.
7. **Biggest remaining lever = more labeled data.** The classifier is trained on only **8 good** and **6 target** listings. CLIP `pro_style` already separates them well, but new pro styles it hasn't seen can still score low. Feed more edge cases via `scripts/add_training_listings.py` and retrain — this is the highest-ceiling improvement.
6. **`scrape_hasdata.py`** (HasData API) remains as a datacenter fallback; it is a separate path from the stealth-browser `scrape_zillow.py`.

---

## 11. Quick commands cheat sheet

```bash
cd /Users/joelwilson/Desktop/realestate-outreach

# Dashboard
./venv/bin/python run.py web --port 5001

# Scan a city / a link
./venv/bin/python run.py scan --city "San Antonio" --state TX --count 40 --keep 5
./venv/bin/python run.py scan --url "https://www.zillow.com/homes/Austin,-TX_rb/"

# Review + send
./venv/bin/python run.py status
./venv/bin/python run.py outreach --max 5
./venv/bin/python run.py outreach --max 5 --send --force

# Teach the photo model
./venv/bin/python scripts/add_training_listings.py good  "<zillow-url>"
./venv/bin/python scripts/add_training_listings.py target "<zillow-url>"
./venv/bin/python src/train_clip.py

# Tests
./venv/bin/python -m pytest tests/test_zillow.py -q
```

---

*Generated June 9, 2026 for agent handoff between Cursor sessions. Pipeline is Zillow-only.*
