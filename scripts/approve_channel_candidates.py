#!/usr/bin/env python3
"""Consume approved channel candidates into the corpus.

Reads P:/.data/yt-is/ef/channel-candidates.json (written by
scripts/channel_candidates.py; approved via operator directive). For every
entry with status=approved:

1. If the channel already exists in channel_metadata -> keep its identity,
   set the domain category (category_source='operator-candidates').
2. Else resolve the channel via yt-dlp channel search (paced), verify the
   returned title loosely matches the candidate name, and upsert the new
   channel row.

The intake pipeline's channel sync (run_intake_pipeline.py Phase 1) then
enumerates the channels on its next run. Entry status becomes 'added'
with applied_at; receipt JSON lands under .logs/channel_candidates_apply/.

Usage:
    python scripts/approve_channel_candidates.py --dry-run
    python scripts/approve_channel_candidates.py --apply
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.batch_status import upsert_channel
from csf.categorize import CATEGORIES, OTHER_CATEGORY
from csf.paths import get_batch_db_path

CANDIDATES_PATH = Path("P:/.data/yt-is/ef/channel-candidates.json")
RECEIPT_DIR = REPO / ".logs" / "channel_candidates_apply"

CATEGORY_MAP = {
    "infra_devops_sre": "Technology",      # no DevOps/SRE category exists
    "security": "Technology",              # no Security category exists
    "software_eng": "Software Engineering",
}

# Channel-ID verified manually (yt-dlp search returns this channel under a
# shifted display title, which the loose title matcher rejects).
MANUAL_OVERRIDES = {
    "Dave Farley (Continuous Delivery)": {
        "url": "https://www.youtube.com/channel/UCCfqyGl3nq_V0bo64CjZh8g",
        "channel_id": "UCCfqyGl3nq_V0bo64CjZh8g",
        "title": "Dave Farley",
    },
}


def resolve_category(domain: str) -> str:
    return CATEGORY_MAP.get(domain, OTHER_CATEGORY)


def find_existing(conn, name: str):
    key = name.split(" (")[0][:20]
    return conn.execute(
        "select channel_url, channel_id, channel_title, category "
        "from channel_metadata where channel_title like ? limit 1",
        (f"%{key}%",)).fetchone()


def resolve_via_ytdlp(name: str) -> dict | None:
    """Resolve a channel name to {url, channel_id, title} via yt-dlp
    channel-filtered search. None = resolution failed."""
    url = ("https://www.youtube.com/results?search_query="
           + quote(name) + "&sp=EgIQAg%3D%3D")
    try:
        r = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--dump-json", "--playlist-end", "3",
             "--no-warnings", url],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if r.returncode != 0:
        return {"error": (r.stderr or "")[:120]}
    for line in r.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        c_url = entry.get("url") or entry.get("webpage_url") or ""
        cid = entry.get("channel_id") or entry.get("id") or ""
        title = entry.get("title") or entry.get("channel") or ""
        if ("channel/" in c_url or cid.startswith("UC")) and title:
            if c_url and not c_url.startswith("http"):
                c_url = "https://www.youtube.com/" + c_url
            return {"url": c_url, "channel_id": cid, "title": title}
    return None


def title_matches(candidate: str, resolved: str) -> bool:
    def toks(s):
        return {t for t in s.lower().replace("(", " ").split() if len(t) > 3}
    c, r = toks(candidate), toks(resolved)
    return bool(c) and len(c & r) >= max(1, len(c) // 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.apply and not args.dry_run:
        args.dry_run = True

    data = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    approved = [c for c in data["candidates"] if c.get("status") == "approved"]
    print(f"approved candidates: {len(approved)} (approved_by: "
          f"{data.get('approved_by')})")

    import sqlite3
    db = get_batch_db_path()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    results = []
    for c in approved:
        name, domain = c["name"], c.get("domain", "")
        cat = resolve_category(domain)
        row = find_existing(conn, c["name"])
        if row:
            rec = {"name": name, "action": "existing", "category": cat,
                   "url": row[0], "channel_id": row[1]}
            print(f"  existing  {name[:38]:40s} -> {row[1]} cat={cat}")
        elif name in MANUAL_OVERRIDES:
            ov = MANUAL_OVERRIDES[name]
            rec = {"name": name, "action": "added", "category": cat,
                   "url": ov["url"], "channel_id": ov["channel_id"],
                   "resolved_title": ov["title"]}
            print(f"  override  {name[:38]:40s} -> {ov['channel_id']} cat={cat}")
        else:
            res = resolve_via_ytdlp(name)
            time.sleep(3.0)
            if not res or "error" in res or not title_matches(name, res.get("title", "")):
                rec = {"name": name, "action": "resolve_failed",
                       "detail": res if res else "no channel result"}
                print(f"  FAILED    {name[:38]:40s} -> "
                      f"{(res or {}).get('error', res or 'title mismatch')}")
                results.append(rec)
                continue
            rec = {"name": name, "action": "added", "category": cat,
                   "url": res["url"], "channel_id": res["channel_id"],
                   "resolved_title": res["title"]}
            print(f"  resolved  {name[:38]:40s} -> {res['channel_id']} "
                  f"| {res['title'][:34]} | cat={cat}")
        rec["apply"] = args.apply
        if args.apply and rec["action"] in ("existing", "added"):
            upsert_channel(
                rec["url"], db_path=db,
                channel_id=rec.get("channel_id"),
                channel_title=rec.get("resolved_title") or name,
                category=rec["category"],
                category_source="operator-candidates-20260822",
            )
        results.append(rec)

    conn.close()

    if args.apply:
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        for c, rec in zip(approved, results):
            if rec.get("apply"):
                c["status"] = "added"
                c["applied_at"] = stamp
                c["channel_id"] = rec.get("channel_id")
        data["applied_at"] = stamp
        CANDIDATES_PATH.write_text(
            json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
        (RECEIPT_DIR / f"apply-{stamp}.json").write_text(
            json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"applied: receipt at {RECEIPT_DIR / ('apply-' + stamp + '.json')}")
    else:
        print("dry run — no writes")
    failed = sum(1 for r in results if r["action"] == "resolve_failed")
    print(f"summary: {len(results) - failed} ok, {failed} failed")
    return 1 if failed and args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
