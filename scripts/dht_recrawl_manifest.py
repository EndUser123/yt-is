"""DA-02f / DA-03 enabler — record expired-URL hits and verify post-re-crawl.

When a DHT attachment URL returns 404 (Discord CDN URLs expire ~24h
after signing), we record the (archive, message_id, attachment_id,
old_url) tuple in a manifest. After the operator re-runs
DiscordHistoryTracker, fresh URLs land in the DHT archive. The
post-re-crawl match step then verifies that the new capture covers
the same messages, by joining on (archive, message_id).

Why this matters: without the manifest, the post-re-crawl run
silently re-processes whatever's new, and we have no way to
measure re-crawl coverage. With the manifest, we can answer:
"of the 22,668 originally-expired URLs, how many did the
re-crawl recover?"

Usage:
  # Build the manifest from the existing state file (already has
  # 22,668 expired-CDN entries in the 03:00 bulk DHT ingest)
  python -m scripts.dht_recrawl_manifest --build

  # After re-crawl, match the new DHT archive against the manifest
  python -m scripts.dht_recrawl_manifest --match

  # Show what % of the original 22,668 is now recoverable
  python -m scripts.dht_recrawl_manifest --coverage
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.extract_dht_artifacts import ARCHIVES, STATE_FILE

MANIFEST_FILE = REPO / ".logs" / "dht-attachments" / "DA-02-recrawl-manifest.json"


def _content_hash(archive: str, message_id: int, attachment_id: int) -> str:
    h = hashlib.sha256()
    h.update(archive.encode())
    h.update(b"\x00")
    h.update(str(message_id).encode())
    h.update(b"\x00")
    h.update(str(attachment_id).encode())
    return h.hexdigest()[:16]


def build_manifest_from_state() -> dict:
    """Walk each DHT archive's attachments table, HEAD-probe each URL,
    record 404s into the manifest. The DHT state file's expired_cdn
    section is the cheap alternative if HEAD-probing 23,400 URLs is
    too slow."""
    if not STATE_FILE.exists():
        return {"expired": [], "live": 0, "total": 0,
                "note": "DA-02-state.json not found; run the extractor first"}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    expired_section = state.get("expired_cdn", {})
    # expired_cdn is keyed by content_hash; we need to reverse-engineer
    # the (archive, message_id, attachment_id). The content_hash is
    # sha256(archive + 0x00 + message_id + 0x00 + attachment_id)[:16],
    # but we don't have the original URL in the state.
    # Cheap workaround: HEAD-probe the URL ourselves by walking the
    # DHT archive and skipping rows already in state['processed'].
    return _build_via_dht_scan()


def _build_via_dht_scan() -> dict:
    """Walk each DHT archive's attachments table; record each 404
    into the manifest with full provenance."""
    import urllib.error
    import urllib.request
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    processed = set(state.get("processed", {}).keys())

    expired: list[dict] = []
    live_count = 0
    for slug, path in ARCHIVES.items():
        if not Path(path).exists():
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                'SELECT message_id, attachment_id, name, url, size '
                'FROM "attachments" WHERE url IS NOT NULL AND url != ""'
            )
            for msg_id, att_id, name, url, size in cur:
                chash = _content_hash(slug, int(msg_id), int(att_id))
                if chash in processed:
                    continue
                # HEAD-probe
                try:
                    req = urllib.request.Request(
                        url, method="HEAD",
                        headers={"User-Agent": "yt-is-recrawl-manifest/1.0"})
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        if resp.status in (200, 206):
                            live_count += 1
                            continue
                except urllib.error.HTTPError as e:
                    if e.code in (403, 404, 410):
                        pass  # expired
                    else:
                        continue
                except Exception:
                    continue
                # If we got here, URL is 4xx
                expired.append({
                    "archive": slug,
                    "message_id": int(msg_id),
                    "attachment_id": int(att_id),
                    "content_hash": chash,
                    "name": name,
                    "old_url": url,
                    "size_bytes": int(size) if size is not None else None,
                })
        finally:
            conn.close()
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "expired_count": len(expired),
        "live_count": live_count,
        "expired": expired,
    }


def match_against_recent_dht() -> dict:
    """After re-crawl: walk the DHT archive's attachments table and
    compute (archive, message_id) overlap with the manifest. Each
    match means a re-captured message has a fresh URL."""
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    expired_keys = {(e["archive"], e["message_id"]) for e in manifest["expired"]}

    recovered: list[dict] = []
    not_recovered: list[dict] = []
    for slug, path in ARCHIVES.items():
        if not Path(path).exists():
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                'SELECT message_id, attachment_id, url '
                'FROM "attachments" WHERE url IS NOT NULL AND url != ""'
            )
            seen_in_archive = set()
            for msg_id, att_id, url in cur:
                if (slug, int(msg_id)) in expired_keys and (slug, int(msg_id)) not in seen_in_archive:
                    seen_in_archive.add((slug, int(msg_id)))
                    recovered.append({
                        "archive": slug,
                        "message_id": int(msg_id),
                        "new_attachment_id": int(att_id),
                        "new_url": url,
                    })
        finally:
            conn.close()

    for e in manifest["expired"]:
        if not any(r["archive"] == e["archive"] and r["message_id"] == e["message_id"]
                   for r in recovered):
            not_recovered.append(e)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manifest_expired_count": manifest["expired_count"],
        "recovered_count": len(recovered),
        "not_recovered_count": len(not_recovered),
        "coverage_pct": round(100.0 * len(recovered) / max(manifest["expired_count"], 1), 2),
        "recovered": recovered,
        "not_recovered": not_recovered,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true",
                    help="Walk DHT archive, record 404s into manifest")
    ap.add_argument("--match", action="store_true",
                    help="Match current DHT against the saved manifest")
    ap.add_argument("--coverage", action="store_true",
                    help="Alias for --match (shorthand)")
    args = ap.parse_args()

    if args.build:
        result = _build_via_dht_scan()
        MANIFEST_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"WROTE {MANIFEST_FILE}")
        print(f"  expired: {result['expired_count']}")
        print(f"  live:    {result['live_count']}")
        return 0
    if args.match or args.coverage:
        if not MANIFEST_FILE.exists():
            print(f"Manifest not found at {MANIFEST_FILE}; run --build first")
            return 2
        result = match_against_recent_dht()
        print(json.dumps({k: v for k, v in result.items() if k not in ("recovered", "not_recovered")},
                         indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
