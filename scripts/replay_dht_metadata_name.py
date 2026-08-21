"""One-shot replay: strip URL query strings from existing
`transcript_cache.metadata_json` `name` field for dht-artifact rows.

The early DA-02 runs (before the metadata sanitizer) wrote the raw `att.name`
which retained the Discord URL query string. The fix in extract_dht_artifacts
only takes effect for newly-processed rows; this script cleans the 661+ rows
already in transcript_cache.

Idempotent: if a row's name is already clean, it's left alone. The check is
`name` contains a `?` AND its length is much larger than the basename
without the query string.

Usage:
  python -m scripts.replay_dht_metadata_name [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef import authority

QUERY_STRIP = re.compile(r"\?.*$")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change but don't write")
    args = ap.parse_args()

    db = authority.TRANSCRIPTS_DB
    if not db.exists():
        print(f"transcript_cache not found at {db}")
        return 2

    conn = sqlite3.connect(str(db))
    updated = 0
    inspected = 0
    already_clean = 0
    samples_changed: list[tuple[str, str, str]] = []  # (cache_key, old, new)
    try:
        cur = conn.execute(
            "SELECT cache_key, metadata_json FROM transcript_cache "
            "WHERE source = 'dht-artifact'"
        )
        rows = cur.fetchall()
        for cache_key, meta_json in rows:
            inspected += 1
            if not meta_json:
                continue
            try:
                meta = json.loads(meta_json)
            except json.JSONDecodeError:
                continue
            name = meta.get("name", "")
            if not name or "?" not in name:
                already_clean += 1
                continue
            clean = QUERY_STRIP.sub("", name).strip() or "attachment"
            if clean == name:
                already_clean += 1
                continue
            meta["name"] = clean
            new_meta_json = json.dumps(meta, ensure_ascii=False)
            if len(samples_changed) < 5:
                samples_changed.append((cache_key, name, clean))
            if not args.dry_run:
                conn.execute(
                    "UPDATE transcript_cache SET metadata_json = ? "
                    "WHERE cache_key = ?",
                    (new_meta_json, cache_key),
                )
            updated += 1
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    print(f"inspected       : {inspected}")
    print(f"already_clean   : {already_clean}")
    print(f"updated         : {updated}{' (dry-run)' if args.dry_run else ''}")
    if samples_changed:
        print("--- samples ---")
        for ck, old, new in samples_changed:
            print(f"  {ck}")
            print(f"    old: {old[:120]}")
            print(f"    new: {new[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
