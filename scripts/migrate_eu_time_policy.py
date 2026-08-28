"""Production migration v1: undated-EU temporal time-policy repair.

Architect-accepted MIXED_SOURCE_TIME_POLICY (2026-08-27). Populates the
provenance table ef.catalog.eu_time_recovery from mechanically recoverable
valid times (Discord/DHT windows + dht-artifact bridge/url-snowflake), so
the temporal read path (ef.concept_discovery._entity_observations) can use
recovered VALID_TIME without ever rewriting eu.published_at and without
substituting captured_at for unknown valid time.

Properties:
  deterministic   recovery keys derive solely from transcript_cache rows +
                  frozen raw archives (no timestamps of execution involved)
  idempotent      UPSERT ... WHERE excluded.basis != basis; a re-run must
                  report changed_rows == 0
  restartable     every batch commits independently; safe re-invocation
  provenance      previous_published_at, method, approx flag, source_field,
                  basis, migration_version, migrated_at stored per row
  fail-closed     refuses --apply when pre-metrics mismatch expectations
                  (--expect-* floors) or when the backup was not taken

Usage:
    python scripts/migrate_eu_time_policy.py plan          # dry-run stats
    python scripts/migrate_eu_time_policy.py apply         # backup + write
    python scripts/migrate_eu_time_policy.py verify        # post checks
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
_scripts = str(REPO / "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import temporal_time_policy as ttp  # noqa: E402

CATALOG_DB = ttp.CATALOG_DB
TRANSCRIPTS_DB = ttp.TRANSCRIPTS_DB
BACKUP_DIR = Path("P:/.data/yt-is/backups")
MIGRATION_VERSION = 1


def _sha_head() -> str:
    h = hashlib.sha256()
    with open(CATALOG_DB, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _retry(fn, attempts=6, delay_s=3.0):
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or i == attempts - 1:
                raise
            time.sleep(delay_s)


def pre_metrics(rec: dict) -> dict:
    rc = sqlite3.connect(f"file:{TRANSCRIPTS_DB.as_posix()}?mode=ro", uri=True)
    cat = sqlite3.connect(f"file:{CATALOG_DB.as_posix()}?mode=ro", uri=True)
    cur = cat.cursor()
    total = cur.execute("SELECT COUNT(*) FROM eu").fetchone()[0]
    undated_total = cur.execute(
        "SELECT COUNT(*) FROM eu WHERE published_at='' OR published_at IS "
        "NULL").fetchone()[0]
    by_source = dict(cur.execute(
        "SELECT source, COUNT(*) FROM eu WHERE published_at='' OR published_at"
        " IS NULL GROUP BY source").fetchall())
    refs = {r[0] for r in cur.execute("SELECT authority_ref FROM eu WHERE "
                                      "source IN ('discord','dht-artifact')")}
    exact = sum(1 for v in rec.values() if not v.get("approx"))
    approx = sum(1 for v in rec.values() if v.get("approx"))
    matched = len(set(rec) & refs)
    unknown = max(undated_total - matched, 0)
    # current production temporal-eligible observation count under the OLD
    # coalesce rule vs the NEW rule (what repair removes)
    old_obs = cur.execute("""
        SELECT COUNT(*) FROM kg_edges m JOIN eu ON eu.eu_id=substr(m.dst_id,4)
        JOIN kg_nodes n ON n.node_id=m.src_id AND n.kind='entity'
        WHERE m.relation='mentioned_in'
          AND substr(COALESCE(NULLIF(eu.published_at,''),eu.captured_at),1,10)
              != ''""").fetchone()[0]
    has_rec = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND "
        "name='eu_time_recovery'").fetchone()
    new_obs = None
    if has_rec:
        new_obs = cur.execute("""
            SELECT COUNT(*) FROM kg_edges m JOIN eu ON eu.eu_id=substr(m.dst_id,4)
            JOIN kg_nodes n ON n.node_id=m.src_id AND n.kind='entity'
            LEFT JOIN eu_time_recovery r ON r.eu_id = eu.eu_id
            WHERE m.relation='mentioned_in'
              AND substr(CASE WHEN NULLIF(eu.published_at,'') IS NOT NULL THEN
                           eu.published_at WHEN r.valid_start IS NOT NULL THEN
                           r.valid_start ELSE '' END,1,10) != ''
                  """).fetchone()[0]
    rc.close()
    cat.close()
    return {
        "total_eus": total,
        "undated_total": undated_total,
        "undated_by_source": by_source,
        "recoverable_matched_eus": matched,
        "recoverable_exact_methods": exact,
        "recoverable_approx_methods": approx,
        "unknown_valid_count": unknown,
        "temporal_mentions_old_rule": old_obs,
        "temporal_mentions_new_rule_pre_populate": new_obs,
    }


def backup_catalog() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dst = BACKUP_DIR / f"catalog.sqlite.pre_eu_time_policy_{ts}"
    src = sqlite3.connect(f"file:{CATALOG_DB.as_posix()}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(str(dst))
    with dst_conn:
        src.backup(dst_conn)
    dst_conn.close()
    sha_src_before = _sha_head()
    return dst, sha_src_before


def populate(dry_run: bool) -> dict:
    """Recovery -> eu_time_recovery upserts keyed by eu authority_ref."""
    rc = sqlite3.connect(f"file:{TRANSCRIPTS_DB.as_posix()}?mode=ro", uri=True)
    rec = ttp.recover_valid_dates(rc)
    rc.close()
    con = sqlite3.connect(str(CATALOG_DB), timeout=30.0)
    con.execute("PRAGMA busy_timeout=30000")
    from ef import catalog as catmod
    catmod.connect(Path(CATALOG_DB))  # ensure table via canonical path
    prev_pub = dict(con.execute(
        "SELECT authority_ref, COALESCE(published_at,'') FROM eu "
        "WHERE source IN ('discord','dht-artifact')"))
    eu_ids = dict(con.execute(
        "SELECT authority_ref, eu_id FROM eu "
        "WHERE source IN ('discord','dht-artifact')"))
    planned = []
    for cache_key, vv in sorted(rec.items()):
        eu_id = eu_ids.get(cache_key)
        if eu_id is None:
            continue
        basis = hashlib.sha256(
            f"{cache_key}|{vv['valid_start']}|{vv['valid_end']}"
            f"|{vv['method']}".encode()).hexdigest()
        planned.append((eu_id, vv["valid_start"], vv["valid_end"],
                        vv["method"], 1 if vv.get("approx") else 0,
                        prev_pub.get(cache_key, ""),
                        f"transcript_cache:{cache_key}:{vv['method']}",
                        basis, MIGRATION_VERSION))
    existing = dict(con.execute(
        "SELECT eu_id, basis FROM eu_time_recovery"))
    changed = [p for p in planned if existing.get(p[0]) != p[7]]
    out = {"planned_rows": len(planned), "changed_rows": len(changed)}
    if dry_run:
        con.close()
        return {**out, "dry_run": True}
    inserted = updated = unchanged = 0
    for i in range(0, len(planned), 2000):
        batch = planned[i:i + 2000]
        def _batch(b=batch):
            n = 0
            for row in b:
                cur = con.execute(
                    """INSERT INTO eu_time_recovery
                       (eu_id, valid_start, valid_end, method, approx,
                        previous_published_at, source_field, basis,
                        migration_version)
                       VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(eu_id) DO UPDATE SET
                         valid_start=excluded.valid_start,
                         valid_end=excluded.valid_end,
                         method=excluded.method,
                         approx=excluded.approx,
                         previous_published_at=excluded.previous_published_at,
                         source_field=excluded.source_field,
                         basis=excluded.basis,
                         migration_version=excluded.migration_version
                       WHERE eu_time_recovery.basis <> excluded.basis""",
                    row)
                n += cur.rowcount if cur.rowcount > 0 else 0
            return n
        n = _retry(_batch)
        con.commit()
        # first-time inserts vs updates distinguished post-hoc below
        updated += n
    after = dict(con.execute("SELECT eu_id, basis FROM eu_time_recovery"))
    inserted = sum(1 for k in after if k not in existing)
    unchanged = len(after) - inserted - sum(
        1 for k in existing if k in after and after[k] != existing[k])
    con.close()
    return {**out, "dry_run": False, "inserted": inserted,
            "updated_in_place": updated, "unchanged_basis": unchanged}


def main_table_counts() -> dict:
    con = sqlite3.connect(f"file:{CATALOG_DB.as_posix()}?mode=ro", uri=True)
    q = con.cursor()
    has_rec = q.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND "
        "name='eu_time_recovery'").fetchone()
    out = {
        "recovery_rows": 0,
        "by_method": {},
        "previous_published_nonempty": 0,
        "eu_published_still_untouched_check": {
            "discord_empty_published":
                q.execute("SELECT COUNT(*) FROM eu WHERE source='discord' AND "
                          "(published_at='' OR published_at IS NULL)"
                          ).fetchone()[0],
            "discord_total":
                q.execute("SELECT COUNT(*) FROM eu WHERE source='discord'"
                          ).fetchone()[0],
        },
        "fabricated_dates_guard": {
            "notebooklm_recovery_rows": 0,
        },
        "captured_preserved_probe": q.execute(
            "SELECT COUNT(*) FROM eu WHERE captured_at='' AND source IN "
            "('discord','dht-artifact')").fetchone()[0],
    }
    if has_rec:
        out["recovery_rows"] = q.execute(
            "SELECT COUNT(*) FROM eu_time_recovery").fetchone()[0]
        out["by_method"] = dict(q.execute(
            "SELECT method, COUNT(*) FROM eu_time_recovery GROUP BY 1"))
        out["previous_published_nonempty"] = q.execute(
            "SELECT COUNT(*) FROM eu_time_recovery "
            "WHERE previous_published_at != ''").fetchone()[0]
        out["fabricated_dates_guard"]["notebooklm_recovery_rows"] = q.execute(
            "SELECT COUNT(*) FROM eu_time_recovery r JOIN eu ON "
            "eu.eu_id=r.eu_id WHERE eu.source='notebooklm'"
        ).fetchone()[0]
    con.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["plan", "apply", "verify"])
    ap.add_argument("--receipt", default=None,
                    help="write JSON receipt here instead of stdout only")
    ap.add_argument("--expect-min-recoverable", type=int, default=28000,
                    help="fail closed when matched recovery rows fall below")
    args = ap.parse_args(argv)

    rc0 = sqlite3.connect(f"file:{TRANSCRIPTS_DB.as_posix()}?mode=ro", uri=True)
    rec = ttp.recover_valid_dates(rc0)
    rc0.close()

    if args.cmd == "verify":
        print(json.dumps(main_table_counts(), indent=2))
        return 0

    metrics = pre_metrics(rec)
    receipt = {"kind": "eu_time_policy_migration_v1", "agent": "zcode",
               "created": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime()),
               "cmd": args.cmd, "pre_metrics": metrics}
    if metrics["recoverable_matched_eus"] < args.expect_min_recoverable:
        receipt["status"] = "refused_below_floor"
        txt = json.dumps(receipt, indent=2)
        if args.receipt:
            Path(args.receipt).write_text(txt)
        print(txt)
        return 2
    if args.cmd == "plan":
        res = populate(dry_run=True)
        receipt["plan"] = res
        receipt["status"] = "dry_run_ok"
    else:
        bak_path, sha_before = backup_catalog()
        res = populate(dry_run=False)
        receipt["backup"] = {"path": str(bak_path),
                             "sha256_head_before_write": sha_before[:16]}
        receipt["apply"] = res
        receipt["post_verify"] = main_table_counts()
        second = populate(dry_run=False)
        receipt["idempotent_rerun"] = {"changed_rows": second["changed_rows"],
                                       "inserted": second.get("inserted", 0)}
        receipt["status"] = ("idempotent_ok"
                             if second["changed_rows"] == 0 and
                             second.get("inserted", 0) == 0
                             else "NON_IDEMPOTENT")
    txt = json.dumps(receipt, indent=2)
    if args.receipt:
        p = Path(args.receipt)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt)
    print(json.dumps({k: receipt[k] for k in
                      ("status", "cmd") if k in receipt}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
