"""Export queued/scored agents to an .xls spreadsheet for review or hand-off.

Usage:
    python src/export_xls.py --top 5 --out austin_agents.xls
    python src/export_xls.py --out all_agents.xls          # whole queue
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import xlwt

sys.path.insert(0, str(Path(__file__).resolve().parent))

from store import get_store

COLUMNS = [
    ("Agent Name", "agent_name", 22),
    ("Phone", "agent_phone", 16),
    ("Email", "agent_email", 28),
    ("Brokerage", "broker_name", 26),
    ("Listing Address", "address", 34),
    ("City", "city", 14),
    ("List Price", "list_price", 12),
    ("Listing URL", "url", 50),
    ("Pro-photo score", "clip_score", 14),
    ("Overall score", "score", 12),
    ("Why (reasons)", "score_reasons", 40),
    ("Status", "status", 10),
    ("Sent At", "sent_at", 20),
    ("Message Sent", "message_sent", 60),
]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def export(rows: list[dict], out_path: Path) -> Path:
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("Agents")

    header_style = xlwt.easyxf(
        "font: bold on, colour white; pattern: pattern solid, fore_colour gray50;"
        "align: vert centre, horiz left;"
    )
    cell = xlwt.easyxf("align: vert top, wrap on;")
    money = xlwt.easyxf("align: vert top;", num_format_str="$#,##0")
    scorefmt = xlwt.easyxf("align: vert top;", num_format_str="0.00")

    for c, (label, _key, width) in enumerate(COLUMNS):
        ws.write(0, c, label, header_style)
        ws.col(c).width = 256 * width
    ws.set_panes_frozen(True)
    ws.set_horz_split_pos(1)

    link = xlwt.easyxf("font: colour blue, underline single; align: vert top;")

    for r, row in enumerate(rows, start=1):
        for c, (_label, key, _w) in enumerate(COLUMNS):
            val = row.get(key, "")
            if key == "list_price":
                ws.write(r, c, _num(val), money)
            elif key in ("score", "clip_score"):
                ws.write(r, c, _num(val), scorefmt)
            elif key == "url" and val:
                safe = str(val).replace('"', "")
                ws.write(r, c, xlwt.Formula(f'HYPERLINK("{safe}","Open listing")'), link)
            else:
                ws.write(r, c, val, cell)

    out_path = out_path.with_suffix(".xls")
    wb.save(str(out_path))
    return out_path


def _clip_key(r: dict) -> float:
    """Sort by how non-professional the photos look (lowest CLIP first)."""
    v = r.get("clip_score")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(r.get("score") or 1.0)


def select_rows(top: int | None, dedupe_agents: bool = True) -> list[dict]:
    from contacts import normalize_phone
    rows = [r for r in get_store().all() if r.get("agent_phone")]
    rows.sort(key=_clip_key)  # least professional-looking photos first
    if dedupe_agents:
        seen, unique = set(), []
        for r in rows:
            p = normalize_phone(r.get("agent_phone", ""))
            if p in seen:
                continue
            seen.add(p)
            unique.append(r)
        rows = unique
    return rows[:top] if top else rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Export agents to .xls")
    ap.add_argument("--top", type=int, default=None, help="only the N weakest-photo listings")
    ap.add_argument("--out", default="agents.xls")
    args = ap.parse_args()
    rows = select_rows(args.top)
    path = export(rows, Path(args.out))
    print(f"Wrote {len(rows)} agents -> {path}")


if __name__ == "__main__":
    main()
