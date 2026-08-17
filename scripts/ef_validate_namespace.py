#!/usr/bin/env python
"""A" section 4: generation-namespace validation (and one-time repair).

Repairs the pre-isolation state: gen1 catalog rows (C-backfill + the A-0
sample rows re-indexed by ef_fixup_a0_overlap.py) had empty build_id and no
production claim. This claims generation 1 under the production BuildSpec,
re-tags those rows, and asserts the invariants:
  1. exactly one production claim per generation, matching buildspec.json
  2. no smoke-kind rows inside production generations
  3. catalog gen1 chunks == qdrant gen1 points (parity)
Exit 0 = valid; 1 = invariant violation (no repair of violations here).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import buildspec, catalog, server
from ef import projection_server as ps


def main() -> int:
    spec = buildspec.load_spec()
    gen = spec["generation"]
    digest = buildspec.spec_digest(spec)
    build_id = f"generation/gen{gen}-{digest}"

    conn = catalog.connect()
    untagged = conn.execute(
        "select count(*) from eu where build_generation=? and build_id=''",
        (gen,)).fetchone()[0]
    if untagged:
        conn.execute(
            "update eu set build_id=? where build_generation=? and build_id=''",
            (build_id, gen))
        conn.commit()
        print(f"[ns] repaired: tagged {untagged:,} gen{gen} rows -> {build_id}")

    catalog.claim_production_generation(conn, gen, build_id, digest)
    print(f"[ns] production claim: gen{gen} -> {build_id} (spec {digest})")

    ok = True
    claims = conn.execute(
        "select generation, build_id, kind, spec_digest from build_claims"
    ).fetchall()
    for g, bid, kind, sd in claims:
        if kind != "production":
            print(f"[ns] VIOLATION: non-production claim on generation {g}")
            ok = False
        if g == gen and (bid != build_id or sd != digest):
            print(f"[ns] VIOLATION: gen{gen} claimed by {bid} ({sd})")
            ok = False
    smoke_in_prod = conn.execute(
        "select count(*) from eu where build_generation=? and build_id like 'smoke/%'",
        (gen,)).fetchone()[0]
    if smoke_in_prod:
        print(f"[ns] VIOLATION: {smoke_in_prod} smoke rows in production gen{gen}")
        ok = False

    cat_chunks = conn.execute(
        "select count(*) from chunk c join eu e on e.eu_id=c.eu_id "
        "where e.build_generation=?", (gen,)).fetchone()[0]
    pts = ps.count(server.client(), gen)
    parity = cat_chunks == pts
    print(f"[ns] parity: catalog gen{gen} chunks={cat_chunks:,} "
          f"points={pts:,} -> {'OK' if parity else 'MISMATCH'}")
    ok &= parity
    conn.close()

    print(f"[ns] RESULT: {'VALID' if ok else 'INVALID'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
