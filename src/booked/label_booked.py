"""Assign booking labels and produce the booked-contacts export.

Labels:
    booked = 1        -> matched to a Square / Cash App payment (ground truth)
    likely_cash = 1   -> no payment match, but the conversation shows strong
                         signs of a completed job (lockbox/Supra access, an
                         email given to receive photos, on-site scheduling,
                         gallery/photos delivered, P2P payment mention). These
                         catch unrecorded cash jobs.

For modeling we use ``booked`` as the positive label and treat ``likely_cash``
rows as ambiguous (excluded from negatives by ``features``/``train``).

Outputs:
    data/booked/labeled_contacts.csv   (per-contact labels + signal score)
    exports/booked_contacts.csv        (deduped person-level booked list)
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .paths import (
    BOOKED_EXPORT_CSV,
    IDENTITIES_CSV,
    IG_THREADS_JSONL,
    IMSG_THREADS_JSONL,
    LABELED_CSV,
    PAYMENTS_CSV,
)

# Each tuple: (weight, compiled regex). Higher weight = stronger booking signal.
_SIGNALS: list[tuple[float, re.Pattern]] = [
    (2.0, re.compile(r"\b(lock\s?box|supra|gate\s?code|door\s?code|access\s?code)\b", re.I)),
    (2.0, re.compile(r"\b(here at|i'?m at|on my way|see you (there|at)|i'?ll be there)\b", re.I)),
    (1.5, re.compile(r"\b(photos?|pics?|gallery|images?|video)\b.*\b(ready|uploaded|sent|done|delivered|attached)\b", re.I)),
    (1.5, re.compile(r"\b(what'?s|good)\b.*\bemail\b.*\b(send|photos?)\b", re.I)),
    (1.5, re.compile(r"\bemail (is|:)?\s*[A-Za-z0-9._%+\-]+@", re.I)),
    (1.5, re.compile(r"\b(venmo|zelle|cash ?app|paid|payment|invoice|deposit|sent (it|you))\b", re.I)),
    (1.0, re.compile(r"\b(schedul|book|appointment|confirm|reschedul)\w*", re.I)),
    (1.0, re.compile(r"\b(what time|tomorrow|today at|this (morning|afternoon|evening)|am or pm)\b", re.I)),
    (1.0, re.compile(r"\b(address|listing) is\b", re.I)),
    (0.5, re.compile(r"\b(thank you|thanks|appreciate|looks great|amazing|perfect)\b", re.I)),
]
_SIGNAL_THRESHOLD = 3.0


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def _signal_score(their_text: str, owner_text: str, replied: bool) -> float:
    if not replied:
        return 0.0  # no reply => no completed job
    blob = f"{their_text}\n{owner_text}"
    score = sum(w for w, rx in _SIGNALS if rx.search(blob))
    # A two-way exchange where the agent shared an email is a strong delivery cue.
    return round(score, 2)


def label(out_labeled: Path = LABELED_CSV, out_export: Path = BOOKED_EXPORT_CSV) -> dict:
    if not IDENTITIES_CSV.exists():
        raise SystemExit("identities.csv missing — run identity_match first.")
    with IDENTITIES_CSV.open(newline="", encoding="utf-8") as f:
        contacts = list(csv.DictReader(f))

    imsg = {r["phone"]: r for r in _load_jsonl(IMSG_THREADS_JSONL) if r.get("phone")}
    ig = {r["handle"]: r for r in _load_jsonl(IG_THREADS_JSONL) if r.get("handle")}

    labeled: list[dict] = []
    for c in contacts:
        booked = c.get("booked") == "1"
        if c["channel"] == "imsg":
            t = imsg.get(c["key"], {})
        else:
            t = ig.get(c["key"], {})
        replied = bool(t.get("replied"))
        score = _signal_score(t.get("their_text", ""), t.get("owner_text", ""), replied)
        likely_cash = int((not booked) and score >= _SIGNAL_THRESHOLD)
        if booked:
            label_source = "payment"
        elif likely_cash:
            label_source = "cash_signal"
        else:
            label_source = "none"
        labeled.append({
            **c,
            "replied": int(replied),
            "signal_score": score,
            "likely_cash": likely_cash,
            "label_source": label_source,
        })

    out_labeled.parent.mkdir(parents=True, exist_ok=True)
    fields = list(labeled[0].keys()) if labeled else []
    with out_labeled.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(labeled)

    _write_export(labeled, out_export)

    n_booked = sum(1 for r in labeled if r["booked"] == "1")
    n_cash = sum(1 for r in labeled if r["likely_cash"] == 1)
    print(
        f"[label] {len(labeled)} contacts labeled -> {out_labeled}\n"
        f"  booked(payment)={n_booked}  likely_cash={n_cash}\n"
        f"  export -> {out_export}"
    )
    return {"booked": n_booked, "likely_cash": n_cash}


def _payment_amounts() -> dict[str, float]:
    """Map payment raw_id -> amount, to dedupe across bridged channels."""
    amounts: dict[str, float] = {}
    if not PAYMENTS_CSV.exists():
        return amounts
    with PAYMENTS_CSV.open(newline="", encoding="utf-8") as f:
        for p in csv.DictReader(f):
            pid = p.get("raw_id") or f"{p['source']}:{p['name']}:{p['date']}"
            try:
                amounts[pid] = float(p.get("amount") or 0)
            except (TypeError, ValueError):
                amounts[pid] = 0.0
    return amounts


def _write_export(labeled: list[dict], out: Path) -> None:
    """Person-level deduped booked list (booked OR likely_cash).

    Payment dollars are summed over the *unique* set of payment ids across all
    of a person's channels, so a bridged iMessage+Instagram match of the same
    invoice is not double counted.
    """
    pay_amounts = _payment_amounts()
    rows = [r for r in labeled if r["booked"] == "1" or r["likely_cash"] == 1]
    by_person: dict[str, dict] = {}
    for r in rows:
        pid = r.get("person_id") or f"{r['channel']}:{r['key']}"
        cur = by_person.get(pid)
        ids = [x for x in (r.get("payment_ids", "") or "").split(";") if x]
        if cur is None:
            by_person[pid] = {
                "name": r["name"],
                "channels": {r["channel"]},
                "phone": r["phone"],
                "handle": r["handle"],
                "payment_ids": set(ids),
                "dates": r.get("payment_dates", ""),
                "label_source": r["label_source"],
                "confidence": float(r["match_confidence"] or 0),
                "signal_score": r["signal_score"],
            }
        else:
            cur["channels"].add(r["channel"])
            cur["payment_ids"].update(ids)
            cur["phone"] = cur["phone"] or r["phone"]
            cur["handle"] = cur["handle"] or r["handle"]
            cur["name"] = cur["name"] or r["name"]
            if r["label_source"] == "payment":
                cur["label_source"] = "payment"
            cur["confidence"] = max(cur["confidence"], float(r["match_confidence"] or 0))

    for cur in by_person.values():
        cur["amount"] = round(sum(pay_amounts.get(i, 0.0) for i in cur["payment_ids"]), 2)
        cur["payment_count"] = len(cur["payment_ids"])
        cur.pop("payment_ids", None)

    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["name", "channels", "phone", "handle", "amount", "payment_count",
              "dates", "label_source", "confidence", "signal_score"]
    ranked = sorted(by_person.values(), key=lambda x: (-x["amount"], -x["confidence"]))
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in ranked:
            r = {**r, "channels": "+".join(sorted(r["channels"])),
                 "amount": round(r["amount"], 2)}
            w.writerow(r)


if __name__ == "__main__":
    label()
