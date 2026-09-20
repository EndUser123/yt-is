#!/usr/bin/env python3
"""Corrected-time replay substrate builder (II Temporal Emergence).

Builds, from the live production catalog, two immutable substrate
snapshots shared by both replay runs:

  catalog-snapshot.sqlite  verbatim file copy — the frozen harness's own
                           reader SQL (published_at -> captured_at fallback)
                           sees exactly the pre-repair semantics.
  corrected-shadow.sqlite  materialized corrected reader per the
                           architect-accepted MIXED_SOURCE_TIME_POLICY v1
                           (production reader ef/concept_discovery.py
                          ::_entity_observations):
                               obs_date = published_at when present
                                 else eu_time_recovery.valid_start
                                 else ''  -> EXCLUDED
                           captured_at is blanked in the derived interface
                           ONLY for excluded rows (no admissible valid
                           time); every admitted row keeps its true
                           recorded time. The underlying snapshot copy is
                           never modified.

Both replay runs point --catalog at these snapshots, so the byte-frozen
harness/evaluator code sees one consistent catalog state and the ONLY
delta between runs is evidence valid-time semantics.

Writes substrate_manifest.json (hashes + counts + exact expressions).
Read-only against the live catalog; writes only under the replay run dir.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

LIVE_CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")

CORRECTED_PUBLISHED_AT = """
CASE WHEN NULLIF(e.published_at,'') IS NOT NULL THEN e.published_at
     WHEN r.valid_start IS NOT NULL THEN r.valid_start
     ELSE '' END"""

CORRECTED_CAPTURED_AT = """
CASE WHEN NULLIF(e.published_at,'') IS NULL
          AND r.valid_start IS NULL THEN ''
     ELSE e.captured_at END"""


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: replay_substrate_builder.py <run-dir>", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1])
    substrate = run_dir / "substrate"
    substrate.mkdir(parents=True, exist_ok=True)

    snap = substrate / "catalog-snapshot.sqlite"
    if not snap.exists():
        shutil.copy2(LIVE_CATALOG, snap)

    corrected = substrate / "corrected-shadow.sqlite"
    if corrected.exists():
        corrected.unlink()
    conn = sqlite3.connect(str(corrected))
    conn.execute("ATTACH DATABASE ? AS snap", (str(snap),))
    eu_cols = [r[1] for r in conn.execute("PRAGMA snap.table_info(eu)")]
    transformed = {"published_at": CORRECTED_PUBLISHED_AT,
                   "captured_at": CORRECTED_CAPTURED_AT}
    select_cols = []
    for c in eu_cols:
        select_cols.append(transformed[c] + f" AS {c}"
                          if c in transformed else f"e.{c} AS {c}")
    conn.execute("CREATE TABLE kg_nodes AS SELECT * FROM snap.kg_nodes")
    conn.execute("CREATE TABLE kg_edges AS SELECT * FROM snap.kg_edges")
    conn.execute(f"""
        CREATE TABLE eu AS
        SELECT {', '.join(select_cols)}
        FROM snap.eu e
        LEFT JOIN snap.eu_time_recovery r ON r.eu_id = e.eu_id""")
    conn.execute("CREATE INDEX ix_eu_id ON eu(eu_id)")
    conn.commit()

    counts = {
        "eu": conn.execute("SELECT count(*) FROM eu").fetchone()[0],
        "kg_nodes": conn.execute(
            "SELECT count(*) FROM kg_nodes").fetchone()[0],
        "kg_edges": conn.execute(
            "SELECT count(*) FROM kg_edges").fetchone()[0],
        "corrected_dated": conn.execute(
            "SELECT count(*) FROM eu WHERE NULLIF(published_at,'') "
            "IS NOT NULL").fetchone()[0],
        "corrected_excluded": conn.execute(
            "SELECT count(*) FROM eu WHERE NULLIF(published_at,'') "
            "IS NULL").fetchone()[0],
    }
    conn.close()

    sconn = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    snap_counts = {
        "eu": sconn.execute("SELECT count(*) FROM eu").fetchone()[0],
        "eu_dated_published": sconn.execute(
            "SELECT count(*) FROM eu WHERE NULLIF(published_at,'') "
            "IS NOT NULL").fetchone()[0],
        "eu_time_recovery": sconn.execute(
            "SELECT count(*) FROM eu_time_recovery").fetchone()[0],
        "kg_edges": sconn.execute(
            "SELECT count(*) FROM kg_edges").fetchone()[0],
        "kg_nodes_entity": sconn.execute(
            "SELECT count(*) FROM kg_nodes WHERE kind='entity'"
        ).fetchone()[0],
    }
    sconn.close()

    manifest = {
        "kind": "corrected_time_replay_substrate",
        "agent": "zcode",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime()),
        "live_catalog": str(LIVE_CATALOG),
        "snapshot": {
            "path": str(snap),
            "sha256": sha256_file(snap),
            "counts": snap_counts,
        },
        "corrected_shadow": {
            "path": str(corrected),
            "sha256": sha256_file(corrected),
            "counts": counts,
        },
        "corrected_reader_expressions": {
            "published_at": " ".join(CORRECTED_PUBLISHED_AT.split()),
            "captured_at": " ".join(CORRECTED_CAPTURED_AT.split()),
            "matches_production_reader":
                "ef/concept_discovery.py::_entity_observations "
                "(MIXED_SOURCE_TIME_POLICY v1)",
        },
        "note": "snapshot is a verbatim file copy (old-reader semantics); "
                "corrected shadow materializes recovered valid time and "
                "excludes unknown-valid rows; eu rows in the snapshot are "
                "never rewritten by the migration (post_verify: "
                "eu_published_rewritten=0)",
    }
    out = substrate / "substrate_manifest.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"snapshot_sha256": manifest["snapshot"]["sha256"],
                      "corrected_sha256":
                          manifest["corrected_shadow"]["sha256"],
                      "counts": counts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
