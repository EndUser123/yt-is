#!/usr/bin/env python
"""B.1 holdout construction (B_GATE_DECISIONS.md Decision 1).

Fresh-video sampling: the frozen benchmark corpus took the FIRST cap=120
videos per category (video_id asc). The holdout draws from videos AFTER
that cap — never present in any model's index during Phase B — so no model
has ever been scored against them.

Outputs:
  --mode excerpts : print stratified excerpts for hand authoring
  --mode auto     : emit identifier + title strata queries (auto-derived)
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from ef import authority, chunking  # noqa: E402

BENCH = REPO / "docs" / "evidence-fabric" / "benchmark"
FROZEN_CAP = 120          # per-category cap used by ef_benchmark_b.py corpus
FRESH_PER_CAT = 12        # videos per category for holdout
IDENT_RE = re.compile(
    r"\b([a-z][a-z0-9]*_(?:[a-z0-9]+_?){2,}[a-z0-9]|\b[a-zA-Z]*[A-Z][a-z]+[A-Z][a-zA-Z]*\b|"
    r"[a-z]+\.(?:exe|py|md|json|toml|db)\b|(?:0x[0-9a-fA-F]{4,}|[A-Z]{2,}[-_]?[0-9]{2,}))")


def fresh_rows():
    conn = sqlite3.connect(f"file:{authority.TRANSCRIPTS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute(f"attach database 'file:{authority.STATUS_DB}?mode=ro' as status")
    rows = conn.execute("""
        select t.video_id, t.transcript, a.title, a.channel_id,
               coalesce(nullif(cm.category,''),'Uncategorized') as category,
               cm.channel_title
        from transcript_cache t
        join status.analysis_status a on a.video_id = t.video_id
        left join status.channel_metadata cm on cm.channel_id = a.channel_id
        where length(t.transcript) >= 100
          and a.channel_id is not null and a.title is not null
        order by t.video_id asc
    """).fetchall()
    conn.close()
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(dict(r))
    fresh = []
    for cat, lst in by_cat.items():
        fresh.extend(lst[FROZEN_CAP:FROZEN_CAP + FRESH_PER_CAT])
    return fresh


def chunk_map(rows):
    out = []
    for x in rows:
        chs = chunking.chunk_transcript(f"{x['video_id']}:transcript",
                                        x["transcript"])
        out.append({"row": x, "chunks": chs})
    return out


def mode_excerpts():
    items = chunk_map(fresh_rows())
    # one substantive mid-transcript chunk per video, up to 90 across cats
    printed = 0
    for it in items:
        if printed >= 90:
            break
        chs = it["chunks"]
        if not chs:
            continue
        mid = chs[min(1, len(chs) - 1)]
        if len(mid.text) < 400:
            continue
        r = it["row"]
        print("=" * 72)
        print(f"video: {r['video_id']}  category: {r['category']}")
        print(f"title: {r['title']}")
        print(f"chunk: {mid.chunk_id}")
        print(f"excerpt: {mid.text[:800]}")
        printed += 1
    print(f"# printed {printed} excerpts")


def mode_auto():
    items = chunk_map(fresh_rows())
    queries = []
    # identifier stratum: exact tokens found in fresh chunks
    ident_seen = set()
    for it in items:
        for ch in it["chunks"]:
            for m in IDENT_RE.findall(ch.text if isinstance(ch.text, str) else ""):
                pass
        # use search on text via finditer on raw string
    for it in items:
        r = it["row"]
        for ch in it["chunks"]:
            for m in IDENT_RE.finditer(ch.text):
                tok = m.group(0)
                if tok.lower() in ident_seen or len(tok) < 5 or len(tok) > 40:
                    continue
                ident_seen.add(tok.lower())
                queries.append({
                    "tier": "holdout", "stratum": "exact_identifiers",
                    "kind": "identifier", "query": tok,
                    "positive_video": r["video_id"],
                    "positive_chunk": ch.chunk_id, "category": r["category"]})
                break  # one identifier per chunk
            if sum(1 for q in queries if q["stratum"] == "exact_identifiers") >= 10:
                break
        if sum(1 for q in queries if q["stratum"] == "exact_identifiers") >= 10:
            break
    # title/entity stratum: distinct, descriptive titles
    n_title = 0
    for it in items:
        r = it["row"]
        t = (r["title"] or "").strip()
        if 20 <= len(t) <= 120 and n_title < 15:
            queries.append({
                "tier": "holdout", "stratum": "title_entity",
                "kind": "title", "query": t,
                "positive_video": r["video_id"], "positive_chunk": None,
                "category": r["category"]})
            n_title += 1
    out = BENCH / "holdout_auto_queries.json"
    out.write_text(json.dumps(queries, indent=1), encoding="utf-8")
    print(f"[auto] {len(queries)} queries -> {out}")
    for q in queries[:12]:
        print(" ", q["stratum"], "|", q["query"][:60], "->", q["positive_video"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["excerpts", "auto"], required=True)
    args = ap.parse_args(argv)
    if args.mode == "excerpts":
        mode_excerpts()
    else:
        mode_auto()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
