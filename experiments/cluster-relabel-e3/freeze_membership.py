"""E3 step 1 — freeze cluster membership BEFORE any labeling work.

Writes an immutable snapshot of the clustering substrate to the private
data dir and prints its sha256. The experiment reads ONLY this snapshot;
the live catalog is never consulted after the freeze.

Snapshot fields per non-series cluster:
  cluster_id, label, top_terms, member_count, video_count
  videos: sorted distinct video_ids joined to eu (title, source,
          channel_id, channel_title, published_at)
  points: chunk point_ids mapped to video_id (for Qdrant dense-vector
          retrieval without touching membership)

Everything is derived from a READ-ONLY connection. Rerunning overwrites
nothing silently: the tool refuses to run if a freeze already exists.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
OUT_DIR = Path("P:/.data/yt-is/ef/cluster-relabel-e3")
SNAPSHOT = OUT_DIR / "membership-frozen.jsonl.gz"


def main() -> int:
    if SNAPSHOT.exists():
        print(f"REFUSED: {SNAPSHOT} already exists — freeze-once semantics", flush=True)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    cur = conn.cursor()

    n_series = cur.execute(
        "SELECT COUNT(*) FROM topic_clusters WHERE is_series=0").fetchone()[0]

    # eu metadata keyed by video_id (one row per video: sources capture one EU
    # per video id in practice; dedupe deterministically by eu_id order)
    eu_rows = cur.execute(
        "SELECT eu_id, video_id, title, source, channel_id, channel_title,"
        " published_at FROM eu ORDER BY eu_id").fetchall()
    eu_by_video: dict[str, dict] = {}
    for eu_id, vid, title, src, ch_id, ch_t, pub in eu_rows:
        if not vid:
            continue
        prev = eu_by_video.get(vid)
        if prev is None or eu_id < prev["_eu_id"]:
            eu_by_video[vid] = {
                "_eu_id": eu_id, "title": title or "", "source": src or "",
                "channel_id": ch_id or "", "channel_title": ch_t or "",
                "published_at": pub or "",
            }
    for rec in eu_by_video.values():
        rec.pop("_eu_id")

    assign_rows = cur.execute(
        "SELECT c.cluster_id, c.video_id, c.point_id"
        " FROM chunk_clusters c JOIN topic_clusters t USING(cluster_id)"
        " WHERE t.is_series = 0 AND c.cluster_id IS NOT NULL"
        " ORDER BY c.cluster_id, c.chunk_id").fetchall()

    members: dict[int, dict[str, set]] = {}
    for cid, vid, pid in assign_rows:
        slot = members.setdefault(int(cid), {"videos": set(), "points": []})
        if vid:
            slot["videos"].add(vid)
            m = eu_by_video.get(vid)
            if m:
                slot["points"].append((vid, int(pid)))

    h = hashlib.sha256()
    frozen_at = datetime.now(timezone.utc).isoformat()
    with SNAPSHOT.open("wb") as rawfh:
        import gzip
        fh = gzip.open(rawfh, "wt", encoding="utf-8", compresslevel=6)
        header = json.dumps({
            "kind": "e3-membership-freeze-v1",
            "frozen_at": frozen_at,
            "catalog": str(CATALOG),
            "n_nonseries_clusters": n_series,
        }, sort_keys=True)
        fh.write(header + "\n")
        h.update(header.encode())
        cluster_rows = cur.execute(
            "SELECT cluster_id, label, top_terms, member_count, video_count"
            " FROM topic_clusters WHERE is_series=0 ORDER BY cluster_id").fetchall()
        kept_videos = 0
        for cid, label, terms_json, mc, vc in cluster_rows:
            slot = members.get(int(cid), {"videos": set(), "points": []})
            vids = sorted(v for v in slot["videos"] if v in eu_by_video)
            rec = {
                "cluster_id": int(cid),
                "label": label or "",
                "top_terms": json.loads(terms_json) if terms_json else [],
                "member_count_chunks": int(mc),
                "video_count_stored": int(vc),
                "videos": [
                    {"video_id": v,
                     **{k: eu_by_video[v][k] for k in
                        ("title", "source", "channel_id",
                         "channel_title", "published_at")}}
                    for v in vids
                ],
                "point_ids": [[v, p] for v, p in sorted(slot["points"])],
            }
            line = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            fh.write(line + "\n")
            h.update(line.encode("utf-8"))
            kept_videos += len(vids)
        fh.close()
    conn.close()

    digest = h.hexdigest()
    summary = {
        "frozen_at": frozen_at,
        "snapshot_path": str(SNAPSHOT),
        "sha256_canonical_jsonl": digest,
        "clusters": len(cluster_rows),
        "member_videos_with_eu": kept_videos,
        "distinct_eu_videos_available": len(eu_by_video),
    }
    (OUT_DIR / "FREEZE.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
