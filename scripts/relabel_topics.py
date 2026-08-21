"""Relabel topic clusters: filter junk tokens, prefer distinctive terms,
and detect series clusters (one channel dominates) so they stop posing
as topics.

Fixes the "Bloomberg Tech 7/30/2026 Close" class of labels:
  1. Date-like tokens, numbers, month/day names, and boilerplate never
     appear in labels.
  2. Label terms are ranked by distinctiveness (rare in other clusters'
     term lists) so a term unique to the cluster leads the label.
  3. Clusters where one channel supplies > SERIES_SHARE of chunks are
     marked is_series=1 and relabeled "Series: <channel>" — real groups,
     but source-shaped, not themes. /trends excludes them.

Usage:
    python scripts/relabel_topics.py            # apply
    python scripts/relabel_topics.py --dry-run  # show before/after
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
BATCH_DB = Path("P:/.data/yt-is/batch_status.sqlite")
SERIES_SHARE = 0.60
LABEL_TERMS = 3

MONTHS = {"january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december",
          "monday", "tuesday", "wednesday", "thursday", "friday",
          "saturday", "sunday", "today", "yesterday", "tomorrow", "tonight",
          "morning", "evening", "close", "closing", "opening", "live",
          "full", "episode", "part", "video", "watch", "new", "top"}

DATE_RE = re.compile(r"^(\d{1,4}([/.-]\d{1,4}){1,2}|20\d\d|x{2,})$")


def junk(term: str) -> bool:
    t = term.strip().lower()
    return (not t or len(t) <= 1 or t in MONTHS or DATE_RE.match(t)
            or t.isdigit())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cat = sqlite3.connect(str(CATALOG), timeout=30.0)
    cat.execute("PRAGMA busy_timeout=30000")
    cat.execute("PRAGMA journal_mode=WAL")
    # schema migration for the series flag
    cols = {r[1] for r in cat.execute(
        "PRAGMA table_info(topic_clusters)").fetchall()}
    if "is_series" not in cols:
        cat.execute("ALTER TABLE topic_clusters ADD COLUMN is_series INTEGER DEFAULT 0")
        cat.commit()

    clusters = cat.execute(
        "SELECT cluster_id, label, top_terms FROM topic_clusters "
        "WHERE cluster_id != -1").fetchall()

    # background document-frequency: how many clusters mention each term
    df: Counter = Counter()
    parsed = {}
    for cid, label, terms_json in clusters:
        terms = []
        try:
            terms = [t.lower().strip() for t in json.loads(terms_json)]
        except Exception:
            continue
        parsed[cid] = terms
        for t in set(terms):
            df[t] += 1

    # channel concentration per cluster (video ownership)
    bs = sqlite3.connect(f"file:{BATCH_DB}?mode=ro", uri=True, timeout=30.0)
    chan_share = {}
    for cid in parsed:
        vids = [r[0] for r in cat.execute(
            "SELECT DISTINCT video_id FROM chunk_clusters WHERE cluster_id = ?",
            (cid,)).fetchall()]
        if not vids:
            chan_share[cid] = (None, 0.0)
            continue
        counter: Counter = Counter()
        for i in range(0, len(vids), 500):
            batch = vids[i:i + 500]
            ph = ",".join("?" for _ in batch)
            for (title,) in bs.execute(
                f"""SELECT COALESCE(cm.channel_title, '?') FROM analysis_status a
                    LEFT JOIN channel_metadata cm ON cm.channel_id = a.channel_id
                    WHERE a.video_id IN ({ph})""", batch):
                counter[title] += 1
        top_chan, top_n = (counter.most_common(1) or [("? ", 0)])[0]
        chan_share[cid] = (top_chan, top_n / max(len(vids), 1))
    bs.close()

    changed = 0
    for cid, old_label, _ in clusters:
        terms = [t for t in parsed.get(cid, []) if not junk(t)]
        # distinctiveness: fewest other clusters share the term
        terms.sort(key=lambda t: (df.get(t, 1), parsed[cid].index(t)
                                  if t in parsed[cid] else 99))
        top_channel, share = chan_share.get(cid, (None, 0.0))
        if share >= SERIES_SHARE and top_channel:
            new_label = f"Series: {top_channel}"
            is_series = 1
        else:
            new_label = " ".join(t.title() for t in terms[:LABEL_TERMS]) \
                or old_label
            is_series = 0
        if new_label != old_label or True:
            changed += 1
            if args.dry_run:
                print(f"  [{cid}] {old_label[:44]!r} -> {new_label[:44]!r}"
                      + ("  [SERIES]" if is_series else ""))
            else:
                cat.execute(
                    "UPDATE topic_clusters SET label = ?, is_series = ? "
                    "WHERE cluster_id = ?", (new_label, is_series, cid))
    cat.commit()
    cat.close()
    n_series = sum(1 for cid in parsed if chan_share[cid][1] >= SERIES_SHARE)
    print(f"{'would relabel' if args.dry_run else 'relabeled'}: {changed} "
          f"clusters; {n_series} marked as series "
          f"(>{int(SERIES_SHARE*100)}% one channel)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
