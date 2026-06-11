"""Generate the Product Design Review (PDR) PDF for the Zillow outreach pipeline.

Pure-Python via reportlab (no system deps). Run:

    ./venv/bin/python scripts/make_pdr_pdf.py

Output: exports/Real_Estate_Outreach_PDR.pdf
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, NextPageTemplate, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "exports" / "Real_Estate_Outreach_PDR.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)

# ── palette ──────────────────────────────────────────────────────────────────
NAVY = colors.HexColor("#0B2545")
BLUE = colors.HexColor("#13315C")
ACCENT = colors.HexColor("#1f6feb")
LIGHT = colors.HexColor("#EEF3FA")
GREY = colors.HexColor("#5b6470")
CODE_BG = colors.HexColor("#0d1117")
CODE_FG = colors.HexColor("#e6edf3")
ROW_ALT = colors.HexColor("#F6F9FD")
LINE = colors.HexColor("#c9d6e5")

# ── styles ───────────────────────────────────────────────────────────────────
ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                    fontSize=17, textColor=NAVY, spaceBefore=18, spaceAfter=8,
                    leading=21)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                    fontSize=12.5, textColor=BLUE, spaceBefore=12, spaceAfter=5,
                    leading=16)
BODY = ParagraphStyle("Body", parent=ss["BodyText"], fontName="Helvetica",
                      fontSize=9.7, textColor=colors.HexColor("#1b1f24"),
                      leading=14.5, spaceAfter=6, alignment=TA_LEFT)
BULLET = ParagraphStyle("Bullet", parent=BODY, leftIndent=14, bulletIndent=4,
                        spaceAfter=3)
CODE = ParagraphStyle("Code", parent=ss["Code"], fontName="Courier",
                      fontSize=8.1, textColor=CODE_FG, leading=11.5,
                      leftIndent=6, rightIndent=6, spaceBefore=2, spaceAfter=2)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.3, textColor=GREY)
TH = ParagraphStyle("TH", parent=BODY, fontName="Helvetica-Bold", fontSize=8.8,
                    textColor=colors.white, leading=12)
TD = ParagraphStyle("TD", parent=BODY, fontSize=8.7, leading=12, spaceAfter=0)
TITLE = ParagraphStyle("Title", parent=ss["Title"], fontName="Helvetica-Bold",
                       fontSize=30, textColor=NAVY, leading=34, alignment=TA_CENTER)
SUB = ParagraphStyle("Sub", parent=BODY, fontSize=13, textColor=ACCENT,
                     alignment=TA_CENTER, leading=18)
COVER_META = ParagraphStyle("CoverMeta", parent=BODY, fontSize=10,
                            textColor=GREY, alignment=TA_CENTER, leading=16)


def esc(t: str) -> str:
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ── flowable helpers ─────────────────────────────────────────────────────────
def para(t, style=BODY):
    return Paragraph(t, style)


def bullets(items):
    return [Paragraph(f"•&nbsp;&nbsp;{t}", BULLET) for t in items]


def code_block(text: str):
    lines = [esc(l) if l else "&nbsp;" for l in text.strip("\n").split("\n")]
    inner = Table([[Paragraph(l, CODE)] for l in lines], colWidths=[6.7 * inch])
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ]))
    return [Spacer(1, 3), inner, Spacer(1, 7)]


def table(header, rows, widths):
    data = [[Paragraph(esc(h), TH) for h in header]]
    for r in rows:
        data.append([Paragraph(esc(str(c)), TD) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    t.setStyle(TableStyle(style))
    return [Spacer(1, 2), t, Spacer(1, 8)]


# ── page furniture ───────────────────────────────────────────────────────────
def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(0.85 * inch, 0.62 * inch, 7.65 * inch, 0.62 * inch)
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(GREY)
    canvas.drawString(0.85 * inch, 0.45 * inch,
                      "Real Estate Photography Outreach — Product Design Review")
    canvas.drawRightString(7.65 * inch, 0.45 * inch, f"Page {doc.page - 1}")
    canvas.restoreState()


def _cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 9.7 * inch, LETTER[0], 1.3 * inch, fill=1, stroke=0)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, 9.55 * inch, LETTER[0], 0.15 * inch, fill=1, stroke=0)
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(
        str(OUT), pagesize=LETTER,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.85 * inch, bottomMargin=0.85 * inch,
        title="Real Estate Outreach — PDR", author="SA Real Estate Media",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")
    cover_frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                        id="cover")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=_cover),
        PageTemplate(id="body", frames=[frame], onPage=_footer),
    ])

    e = []  # story

    # ── COVER ────────────────────────────────────────────────────────────────
    e.append(Spacer(1, 2.1 * inch))
    e.append(para("PRODUCT DESIGN REVIEW", SUB))
    e.append(Spacer(1, 0.15 * inch))
    e.append(para("Real Estate Photography<br/>Outreach Pipeline", TITLE))
    e.append(Spacer(1, 0.2 * inch))
    e.append(para("Zillow-Only Scraping &amp; CLIP Photo-Quality Targeting", SUB))
    e.append(Spacer(1, 1.5 * inch))
    e.append(para("SA Real Estate Media", COVER_META))
    e.append(para(f"Document generated: {datetime.now():%B %d, %Y}", COVER_META))
    e.append(para("Status: Live · Source: Zillow · Model: CLIP k-NN (97% LOO)",
                  COVER_META))
    e.append(NextPageTemplate("body"))
    e.append(PageBreak())

    # ── 1. EXECUTIVE SUMMARY ──────────────────────────────────────────────────
    e.append(para("1. Executive Summary", H1))
    e.append(para(
        "This is a macOS-local lead-generation tool for <b>SA Real Estate Media</b>. "
        "It scrapes active <b>Zillow</b> listings, uses a CLIP-based image classifier to "
        "identify listings whose photos look <b>amateur / phone-quality</b>, and queues "
        "those listings' agents for a personalized cold <b>iMessage</b> offering professional "
        "real-estate photography. The premise: agents with weak listing photos are the highest-"
        "intent prospects for a photography service.", BODY))
    e.append(para(
        "During this engagement the pipeline was consolidated from a multi-source scraper "
        "stack (Redfin, Unlock MLS, Tavily) down to a single, robust <b>Zillow stealth-browser "
        "scraper</b>, the front end was rebuilt around it (including a paste-a-link feature), and "
        "the photo-quality model was retrained to correctly treat <b>twilight/dusk</b> exteriors as "
        "professional and to catch previously-missed bad listings.", BODY))

    e.append(para("Outcomes at a glance", H2))
    e += table(
        ["Metric", "Before", "After"],
        [
            ["Listing sources", "Redfin + Unlock MLS + Tavily + Zillow", "Zillow only"],
            ["Source scraper LOC deleted", "—", "~3 modules + 2 test files removed"],
            ["Paste-a-Zillow-link scan", "No", "Yes (CLI + dashboard)"],
            ["Twilight exteriors", "Mislabeled amateur (false positives)", "Professional (0.82–0.89)"],
            ["Model training set", "good ~58 / target ~136", "good 283 / target 204"],
            ["Leave-one-out accuracy", "n/a (tiny set)", "~97%"],
            ["Active queue", "124 mixed/old rows", "16 fresh (backup kept)"],
        ],
        [1.7 * inch, 2.6 * inch, 2.4 * inch])

    # ── 2. PROBLEM & OBJECTIVES ───────────────────────────────────────────────
    e.append(para("2. Problem Statement &amp; Objectives", H1))
    e.append(para("Problems addressed", H2))
    e += bullets([
        "The workflow depended on several brittle scrapers; the user wanted a single Zillow source.",
        "The dashboard still surfaced the old sources and would not refresh to show Zillow.",
        "The CLIP classifier flagged <b>twilight/dusk</b> professional exteriors as amateur "
        "(false positives) and let some genuinely bad listings through (false negatives).",
        "The queue was cluttered with stale listings from prior runs.",
    ])
    e.append(para("Objectives", H2))
    e += bullets([
        "Port the user's standalone Zillow scraper technique into the production pipeline.",
        "Make Zillow the sole source; remove Redfin / Unlock MLS / Tavily cleanly.",
        "Add the ability to scan a pasted Zillow URL (search page or a single listing).",
        "Retrain the photo model from real labeled listings until twilight = professional "
        "and obvious bad listings are caught.",
        "Provide a reusable labeling tool and reset the queue to only fresh listings.",
    ])

    # ── 3. ARCHITECTURE ───────────────────────────────────────────────────────
    e.append(para("3. System Architecture", H1))
    e.append(para(
        "Two parallel pipelines share a config, store, and Flask dashboard. This engagement "
        "focused on the Zillow / iMessage pipeline; the Instagram pipeline is unchanged.", BODY))
    e += code_block(
        "run.py (CLI)                 src/web/app.py (Flask dashboard, :5001)\n"
        "      |                                |\n"
        "      v                                v\n"
        "  Zillow / iMessage pipeline      Instagram pipeline (unchanged)\n"
        "      |                                |\n"
        "  scrape_zillow --> scan --> store   ig_discover --> ig_store\n"
        "      |              |    (queue.csv)      |             |\n"
        "      v              v                     v             v\n"
        "  score_listings <- score.py + heuristics  ig_rank/llm  ig_dm\n"
        "      |              |                      |\n"
        "      v              v                      v\n"
        "  outreach --> imessage + templates    ig_browser (Playwright)")
    e.append(para("Data flow (Zillow scan)", H2))
    e += bullets([
        "<b>Phase 1 — discover (cheap):</b> fetch the Zillow search page / pasted link, parse "
        "~40 result cards from the <font face='Courier'>__NEXT_DATA__</font> JSON. Each card has a "
        "hero photo + price + address. Hero is CLIP-scored; keep the worst N.",
        "<b>Phase 2 — enrich (only kept):</b> open each kept <font face='Courier'>/homedetails/</font> "
        "page; read listing-agent name/phone/brokerage from the <font face='Courier'>gdpClientCache</font> "
        "graph and the full gallery; full-score the gallery; store contactable rows.",
        "<b>Filters:</b> virtual-tour listings skipped in Phase 1; drone/aerial skipped in Phase 2; "
        "only listings with a valid human listing-agent name are queued.",
    ])

    # ── 4. ZILLOW SCRAPER ─────────────────────────────────────────────────────
    e.append(para("4. The Zillow Scraper (src/scrape_zillow.py)", H1))
    e.append(para(
        "Ported from the standalone <font face='Courier'>~/Desktop/zillow-scraper</font>. It drives "
        "Scrapling's <b>StealthySession</b> (a stealth Playwright browser) to defeat Zillow's "
        "PerimeterX / reCAPTCHA bot wall — no API keys or credits.", BODY))
    e.append(para("Key techniques", H2))
    e += bullets([
        "<b>Search parsing:</b> listings come from the embedded "
        "<font face='Courier'>__NEXT_DATA__</font> JSON (<font face='Courier'>listResults</font>), "
        "with an inline <font face='Courier'>application/json</font> fallback. Far more reliable than "
        "DOM scraping (cards lazy-load).",
        "<b>Detail / agent parsing:</b> re-parse "
        "<font face='Courier'>props.pageProps.componentProps.gdpClientCache</font> (a stringified JSON "
        "graph) and DFS-walk it for agent attribution keys "
        "(<font face='Courier'>agentName, agentPhoneNumber, brokerName, brokerPhoneNumber</font>).",
        "<b>Galleries:</b> full-res photos scraped from "
        "<font face='Courier'>photos.zillowstatic.com/fp/...</font>, de-duplicated by photo hash.",
        "<b>Hygiene:</b> phone formatting + toll-free rejection; a valid listing agent must be a "
        "2-token human name (rejects 'Zillow', 'Premier Agent').",
        "<b>Testability:</b> the pure HTML parsers use stdlib regex (no Scrapling import), so they "
        "unit-test without browser dependencies.",
    ])
    e.append(para("Public interface", H2))
    e += table(
        ["Function", "Purpose"],
        [
            ["discover_listings(city, state, max_urls)", "Phase 1 from a city/state search"],
            ["discover_listings_from_url(url, max_urls)", "Phase 1 from a pasted link (search or one listing)"],
            ["enrich_agents(listings, ...)", "Phase 2: detail pages -> agent contact + gallery"],
            ["parse_search_cards(html) / parse_detail(html)", "Pure HTML -> Listing parsers (tested)"],
            ["valid_listing_agent(name)", "True if a real person's name"],
            ["build_search_url(city, state)", "City -> Zillow search URL"],
            ["scrape(city, state, max, enrich)", "Standalone discover + optional enrich"],
        ],
        [3.0 * inch, 3.7 * inch])

    # ── 5. PHOTO MODEL ────────────────────────────────────────────────────────
    e.append(para("5. Photo-Quality Model", H1))
    e.append(para(
        "A k-nearest-neighbor classifier in CLIP embedding space "
        "(<font face='Courier'>openai/clip-vit-base-patch32</font>, MPS on Mac). Each photo is "
        "compared against two labeled reference sets and scored for professionalism; a separate "
        "zero-shot prompt detects drone/aerial shots.", BODY))
    e.append(para("Scoring components", H2))
    e += table(
        ["Signal", "Source", "Meaning"],
        [
            ["pro_prob (0-1)", "k-NN vs good/target embeddings", "Higher = more professional"],
            ["aerial_prob (0-1)", "CLIP zero-shot prompts", ">= 0.6 => drone, listing skipped"],
            ["vertical_score", "OpenCV line straightness", "Camera level = 1.0, tilted = 0.0"],
            ["clip_score (UI)", "0.5*pro_style + 0.5*vertical", "Lower = worse photos = target"],
        ],
        [1.5 * inch, 2.7 * inch, 2.5 * inch])
    e.append(para("Why twilight broke it (and the fix)", H2))
    e.append(para(
        "The original 'good' set was tiny (~2 listings) and contained no dusk/twilight examples, so "
        "warm-sky exteriors landed closer to the amateur cluster. The fix was not a code change but a "
        "<b>data change</b>: label real listings and retrain. A reusable tool "
        "(<font face='Courier'>scripts/add_training_listings.py</font>) downloads any listing's full "
        "gallery into the chosen bucket.", BODY))
    e.append(para("Verification after retraining", H2))
    e += table(
        ["Sample", "pro_prob", "Verdict"],
        [
            ["Twilight — 2114 Fort Donelson", "0.82", "Professional"],
            ["Twilight — 11725 Red Oak Valley", "0.89", "Professional"],
            ["Twilight — 6105 Highlandale", "0.89", "Professional"],
            ["Twilight — 1911 Far Niente", "0.85", "Professional"],
            ["Professional — 2206 Parkway", "0.78", "Professional"],
            ["Bad — 26934 Oleander Chase", "0.19", "Amateur (now caught)"],
            ["Amateur control", "0.05", "Amateur"],
        ],
        [3.0 * inch, 1.4 * inch, 2.3 * inch])
    e.append(para(
        "Training set grew to <b>good = 283</b> and <b>target = 204</b> images; leave-one-out "
        "accuracy ≈ <b>97%</b>.", SMALL))

    # ── 6. RETRAIN WORKFLOW ───────────────────────────────────────────────────
    e.append(para("6. Retraining Workflow", H1))
    e.append(para("To keep tuning the model as new edge cases appear:", BODY))
    e += code_block(
        "# Professional listing (incl. twilight) -> good bucket\n"
        "./venv/bin/python scripts/add_training_listings.py good  \"<zillow-url>\"\n\n"
        "# Amateur / bad listing -> target bucket\n"
        "./venv/bin/python scripts/add_training_listings.py target \"<zillow-url>\"\n\n"
        "# Rebuild CLIP prototypes (prints leave-one-out accuracy)\n"
        "./venv/bin/python src/train_clip.py")
    e.append(para(
        "Photos are saved under "
        "<font face='Courier'>training_data/&lt;good|target&gt;/&lt;slug&gt;_&lt;zpid&gt;/NN.jpg</font>. "
        "<font face='Courier'>train_clip.py</font> writes "
        "<font face='Courier'>good_embeddings.npy</font>, "
        "<font face='Courier'>target_embeddings.npy</font>, and "
        "<font face='Courier'>good_centroid.npy</font> into <font face='Courier'>models/</font>; the next "
        "scan picks them up automatically.", BODY))

    # ── 7. FRONT END ──────────────────────────────────────────────────────────
    e.append(para("7. Front End &amp; API", H1))
    e.append(para(
        "The Flask dashboard (<font face='Courier'>http://127.0.0.1:5001</font>) was simplified to "
        "Zillow only: the Source dropdown was removed and a <b>Zillow link</b> field added that posts a "
        "<font face='Courier'>url</font> to the scan endpoint (overrides city/state).", BODY))
    e += table(
        ["Endpoint", "Method", "Purpose"],
        [
            ["/api/listings", "GET", "Queue rows + status counts"],
            ["/api/scan", "POST", "Background scan (city/state/count/keep/max_pro_score/url)"],
            ["/api/scan/status", "GET", "Poll scan progress"],
            ["/api/preview", "POST", "Message preview for one listing"],
            ["/api/send + /status", "POST/GET", "Background iMessage send + results"],
            ["/api/export + /download", "POST/GET", "Build + download .xls"],
        ],
        [1.9 * inch, 0.9 * inch, 3.9 * inch])
    e.append(para(
        "<b>Operational note:</b> Flask runs with <font face='Courier'>debug=False</font>, so it caches "
        "templates in memory. A 'dashboard not updating' issue this session was a stale server holding "
        "the old template — always <b>restart the server after code/template changes</b>.", BODY))

    # ── 8. CLI ────────────────────────────────────────────────────────────────
    e.append(para("8. CLI Reference (run.py)", H1))
    e += table(
        ["Command", "Notes"],
        [
            ["scan", "Zillow-only. --city --state --url --count --keep --max-pro-score"],
            ["scrape", "Raw -> listings.json. --source {realtor, zillow, hasdata} (default zillow)"],
            ["pipeline", "scrape + score + enqueue"],
            ["score / enqueue", "Score JSON / load into store"],
            ["outreach", "Send iMessages to queued rows (--send, --force)"],
            ["dnc / status / export", "DNC add / queue summary / .xls export"],
            ["web", "Flask dashboard"],
            ["ig-*", "Instagram pipeline (unchanged)"],
        ],
        [1.5 * inch, 5.2 * inch])
    e += code_block(
        "cd /Users/joelwilson/Desktop/realestate-outreach\n"
        "./venv/bin/python run.py web --port 5001\n"
        "./venv/bin/python run.py scan --city \"San Antonio\" --state TX --count 40 --keep 5\n"
        "./venv/bin/python run.py scan --url \"https://www.zillow.com/homes/Austin,-TX_rb/\"\n"
        "./venv/bin/python run.py status\n"
        "./venv/bin/python run.py outreach --max 5 --send --force")

    # ── 9. CHANGE LOG ─────────────────────────────────────────────────────────
    e.append(para("9. Change Log (this engagement)", H1))
    e += table(
        ["#", "Change"],
        [
            ["1", "Ported the Zillow scraper into the pipeline (src/scrape_zillow.py) + 8 unit tests."],
            ["2", "Wired Zillow into scan.py, run.py, config.py, and the web app/UI."],
            ["3", "Removed Redfin / Unlock MLS / Tavily scrapers + tests; Zillow-only."],
            ["4", "Added paste-a-link support (discover_listings_from_url + dashboard field)."],
            ["5", "Fixed stale dashboard by restarting the Flask server (template cache)."],
            ["6", "Retrained model for twilight: +6 pro listings (225 imgs) -> 98% LOO."],
            ["7", "Caught a missed bad listing: +Oleander Chase (68 imgs) -> 97% LOO."],
            ["8", "Built scripts/add_training_listings.py (reusable labeling tool)."],
            ["9", "Cleared the queue to 16 fresh rows; full backup kept."],
            ["10", "Verified end-to-end with Austin + San Antonio scans."],
        ],
        [0.4 * inch, 6.3 * inch])

    # ── 10. RISKS & OPEN ITEMS ────────────────────────────────────────────────
    e.append(para("10. Risks, Gotchas &amp; Open Items", H1))
    e += bullets([
        "<b>Flask template cache:</b> restart the server after any code/template change.",
        "<b>Lost send history:</b> clearing old queue rows removed past 'sent' status (preserved in "
        "<font face='Courier'>data/queue_backup_20260609_103948.csv</font>). A re-scan could re-queue "
        "previously-contacted agents until that history is merged back.",
        "<b>Zillow throttling:</b> detail pages occasionally hit 30s timeouts; the scraper auto-retries. "
        "A residential proxy + lower rate helps at volume.",
        "<b>Model is data-driven:</b> accuracy improves as more edge cases are labeled and retrained.",
        "<b>clip_score semantics:</b> it is a style+vertical blend (lower = worse = target), not raw CLIP.",
        "<b>HasData path retained:</b> scrape_hasdata.py (Zillow via API) remains as a datacenter fallback, "
        "separate from the stealth-browser scraper.",
    ])

    e.append(Spacer(1, 0.2 * inch))
    e.append(para(
        "See <font face='Courier'>PROJECT_HANDOFF.md</font> in the repo root for the full operational "
        "handoff, folder map, and command cheat sheet.", SMALL))

    doc.build(e)
    return OUT


if __name__ == "__main__":
    out = build()
    print(f"Wrote {out}  ({out.stat().st_size/1024:.0f} KB)")
