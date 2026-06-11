"""Local web dashboard for the real-estate outreach tool.

A single-page control panel over the existing pipeline:
  - Listings tab: scan, browse, preview, and send iMessages
  - IG DMs tab: discover realtors on Instagram, preview, and send DMs

Run it with:  python run.py web   (then open http://127.0.0.1:5000)
"""
from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import outreach  # noqa: E402
import ig_dm  # noqa: E402
import ig_discover  # noqa: E402
import scan as scan_mod  # noqa: E402
import templates as templates_mod  # noqa: E402
from config import EXPORTS_DIR, settings  # noqa: E402
from ig_store import get_ig_store, normalize_handle  # noqa: E402
from store import get_store  # noqa: E402

app = Flask(__name__)

# In-memory job state (single user, local tool — a dict is plenty).
JOBS: dict[str, dict] = {}
SEND_JOBS: dict[str, dict] = {}
IG_JOBS: dict[str, dict] = {}
IG_SEND_JOBS: dict[str, dict] = {}


def _settings_dict() -> dict:
    return {
        "business_name": settings.business_name,
        "sender_first_name": settings.sender_first_name,
        "contact_phone": settings.contact_phone,
        "website": settings.website,
        "service_area": settings.service_area,
        "special_offer": settings.special_offer,
        "daily_send_cap": settings.daily_send_cap,
        "send_hour_start": settings.send_hour_start,
        "send_hour_end": settings.send_hour_end,
        "score_threshold": settings.outreach_score_threshold,
        "ig_daily_dm_cap": settings.ig_daily_dm_cap,
        "ig_send_mode": settings.ig_send_mode,
        "ig_llm_enabled": settings.ig_llm_enabled and bool(settings.openai_api_key),
        "ig_llm_mode": settings.ig_llm_mode,
        "ig_agent_mode": settings.ig_llm_mode == "agent" and bool(settings.openai_api_key),
    }


@app.route("/")
def index():
    return render_template("index.html", settings=_settings_dict())


@app.route("/api/listings")
def api_listings():
    rows = get_store().all()

    def _key(r):
        try:
            return float(r.get("clip_score") or 1.0)
        except (TypeError, ValueError):
            return 1.0

    rows.sort(key=_key)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "new")] = counts.get(r.get("status", "new"), 0) + 1
    return jsonify({"listings": rows, "counts": counts, "total": len(rows)})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    lid = (request.json or {}).get("listing_id", "")
    row = get_store().get(str(lid))
    if not row:
        return jsonify({"error": "listing not found"}), 404
    # Fixed seed so the preview matches a stable variant for this listing.
    msg = templates_mod.render(row, seed=hash(str(lid)) & 0xFFFF)
    return jsonify({"message": msg, "phone": row.get("agent_phone", ""),
                    "agent_name": row.get("agent_name", "")})


def _run_send(job_id: str, ids: list[str], dry_run: bool, force: bool):
    job = SEND_JOBS[job_id]
    try:
        out = outreach.send_selected_detailed(ids, dry_run=dry_run, force_hours=force)
        job.update(phase="done", running=False, **out)
    except Exception as e:
        job.update(phase="error", running=False, message=str(e))


@app.route("/api/send", methods=["POST"])
def api_send():
    if any(j.get("running") for j in SEND_JOBS.values()):
        return jsonify({"error": "a send is already in progress"}), 409
    data = request.json or {}
    ids = [str(i) for i in data.get("listing_ids", [])]
    dry_run = bool(data.get("dry_run", True))
    force = bool(data.get("force", False))
    if not ids:
        return jsonify({"error": "no listings selected"}), 400

    job_id = uuid.uuid4().hex[:8]
    SEND_JOBS[job_id] = {
        "running": True,
        "phase": "sending",
        "message": f"{'Previewing' if dry_run else 'Sending'} {len(ids)} message(s)…",
        "dry_run": dry_run,
    }
    t = threading.Thread(target=_run_send, args=(job_id, ids, dry_run, force), daemon=True)
    t.start()
    return jsonify({"job_id": job_id, "dry_run": dry_run})


@app.route("/api/send/status")
def api_send_status():
    job_id = request.args.get("job_id", "")
    job = SEND_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.route("/api/dnc", methods=["POST"])
def api_dnc():
    data = request.json or {}
    phone = str(data.get("phone", "")).strip()
    if not phone:
        return jsonify({"error": "phone required"}), 400
    store = get_store()
    store.add_dnc(phone, reason=data.get("reason", "manual (web)"))
    row = store.find_by_phone(phone)
    if row:
        store.update(row["listing_id"], status="dnc")
    return jsonify({"ok": True, "phone": phone})


@app.route("/api/export", methods=["POST"])
def api_export():
    import export_xls

    data = request.json or {}
    limit = int(data.get("limit", 5))
    out = EXPORTS_DIR / "bad_photo_listings.xls"
    rows = export_xls.select_rows(top=limit, dedupe_agents=True)
    if not rows:
        return jsonify({"error": "no listings to export"}), 400
    export_xls.export(rows, out)
    return jsonify({"ok": True, "path": str(out), "count": len(rows)})


@app.route("/api/export/download")
def api_export_download():
    out = EXPORTS_DIR / "bad_photo_listings.xls"
    if not out.exists():
        return jsonify({"error": "no export yet"}), 404
    return send_file(out, as_attachment=True, download_name="bad_photo_listings.xls")


# ---- scan (background) ----------------------------------------------------
def _run_scan(job_id: str, city: str, count: int, keep: int, max_pro,
              state: str, url: str):
    job = JOBS[job_id]
    try:
        def progress(info: dict):
            job.update(info)
        scan_mod.scan(city=city, count=count, keep=keep, max_pro_score=max_pro,
                      state=state, url=url or None, progress=progress)
        job["phase"] = "done"
        job["running"] = False
    except Exception as e:  # surface scrape/network failures to the UI
        job["phase"] = "error"
        job["message"] = f"Scan failed: {e}"
        job["running"] = False


@app.route("/api/scan", methods=["POST"])
def api_scan():
    if any(j.get("running") for j in JOBS.values()):
        return jsonify({"error": "a scan is already running"}), 409
    data = request.json or {}
    city = data.get("city", "Austin")
    count = int(data.get("count", 200))
    keep = int(data.get("keep", 15))
    state = data.get("state", "TX")
    url = (data.get("url") or "").strip()
    max_pro = data.get("max_pro_score")
    max_pro = float(max_pro) if max_pro not in (None, "") else None

    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"running": True, "phase": "starting",
                    "message": "Starting scan…", "city": city}
    t = threading.Thread(target=_run_scan,
                         args=(job_id, city, count, keep, max_pro, state, url),
                         daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/scan/status")
def api_scan_status():
    job_id = request.args.get("job_id", "")
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


# ---- Instagram -----------------------------------------------------------
@app.route("/api/ig/profiles")
def api_ig_profiles():
    rows = get_ig_store().all()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "new")] = counts.get(r.get("status", "new"), 0) + 1
    def _bs(r):
        try:
            return float(r.get("book_score") or 0)
        except (TypeError, ValueError):
            return 0.0
    # Highest booking-propensity first, then alphabetical.
    rows.sort(key=lambda r: (-_bs(r), (r.get("display_name") or r.get("ig_handle") or "").lower()))
    return jsonify({"profiles": rows, "counts": counts, "total": len(rows)})


@app.route("/api/ig/preview", methods=["POST"])
def api_ig_preview():
    handle = normalize_handle((request.json or {}).get("ig_handle", ""))
    row = get_ig_store().get(handle)
    if not row:
        return jsonify({"error": "profile not found"}), 404
    msg = templates_mod.render_instagram(row, seed=hash(handle) & 0xFFFF)
    return jsonify({
        "message": msg,
        "ig_handle": handle,
        "display_name": row.get("display_name", ""),
        "profile_url": row.get("profile_url", ""),
    })


def _run_ig_discover(
    job_id: str,
    city: str,
    max_results: int,
    enrich: bool,
    realtor_score_threshold: float | None,
):
    job = IG_JOBS[job_id]
    try:
        def progress(info: dict):
            job.update(info)
        stats = ig_discover.discover(
            city=city,
            max_results=max_results,
            enrich_from_queue=enrich,
            realtor_score_threshold=realtor_score_threshold,
            progress=progress,
        )
        job.update(phase="done", running=False, stats=stats,
                   message=(f"Matched {stats.get('matched', 0)} profiles"
                            f" · skipped {stats.get('skipped_following', 0)} following"
                            f" · {stats.get('skipped_low_score', 0)} low score"
                            f" · {stats.get('skipped_semantic_dup', 0)} semantic dup"))
    except Exception as e:
        job.update(phase="error", running=False, message=str(e))


@app.route("/api/ig/discover", methods=["POST"])
def api_ig_discover():
    if any(j.get("running") for j in IG_JOBS.values()):
        return jsonify({"error": "an Instagram discover job is already running"}), 409
    data = request.json or {}
    city = data.get("city", "San Antonio")
    max_results = int(data.get("max", 30))
    enrich = bool(data.get("enrich_from_queue", False))
    realtor_threshold = data.get("realtor_score_threshold")
    realtor_score_threshold = float(realtor_threshold) if realtor_threshold not in (None, "") else None
    job_id = uuid.uuid4().hex[:8]
    IG_JOBS[job_id] = {"running": True, "phase": "starting", "message": "Starting discover…", "city": city}
    t = threading.Thread(
        target=_run_ig_discover,
        args=(job_id, city, max_results, enrich, realtor_score_threshold),
        daemon=True,
    )
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/ig/discover/status")
def api_ig_discover_status():
    job_id = request.args.get("job_id", "")
    job = IG_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


def _run_ig_send(job_id: str, handles: list[str], dry_run: bool, force: bool, automated: bool):
    job = IG_SEND_JOBS[job_id]
    try:
        out = ig_dm.send_selected_detailed(handles, dry_run=dry_run, force_hours=force, automated=automated)
        job.update(phase="done", running=False, **out)
    except Exception as e:
        job.update(phase="error", running=False, message=str(e))


@app.route("/api/ig/send", methods=["POST"])
def api_ig_send():
    if any(j.get("running") for j in IG_SEND_JOBS.values()):
        return jsonify({"error": "an Instagram send is already in progress"}), 409
    data = request.json or {}
    handles = [normalize_handle(str(h)) for h in data.get("ig_handles", [])]
    handles = [h for h in handles if h]
    dry_run = bool(data.get("dry_run", True))
    force = bool(data.get("force", False))
    automated = bool(data.get("automated", settings.ig_send_mode == "automated"))
    if not handles:
        return jsonify({"error": "no profiles selected"}), 400
    job_id = uuid.uuid4().hex[:8]
    IG_SEND_JOBS[job_id] = {
        "running": True,
        "phase": "sending",
        "message": f"{'Previewing' if dry_run else 'Sending'} {len(handles)} DM(s)…",
        "dry_run": dry_run,
    }
    t = threading.Thread(target=_run_ig_send, args=(job_id, handles, dry_run, force, automated), daemon=True)
    t.start()
    return jsonify({"job_id": job_id, "dry_run": dry_run})


@app.route("/api/ig/send/status")
def api_ig_send_status():
    job_id = request.args.get("job_id", "")
    job = IG_SEND_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.route("/api/ig/dnc", methods=["POST"])
def api_ig_dnc():
    data = request.json or {}
    handle = normalize_handle(str(data.get("ig_handle", "")))
    if not handle:
        return jsonify({"error": "ig_handle required"}), 400
    store = get_ig_store()
    store.add_dnc(handle, reason=data.get("reason", "manual (web)"))
    return jsonify({"ok": True, "ig_handle": handle})


def main(host: str = "127.0.0.1", port: int = 5000):
    print(f"\n  Outreach dashboard → http://{host}:{port}\n")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
