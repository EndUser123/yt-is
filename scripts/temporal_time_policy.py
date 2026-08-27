"""Temporal Evidence Unit time-policy counterfactual (undated-EU lanes).

Packet-driven diagnostic lane (2026-08-26): determine how undated EUs
(Discord/DHT bulk captures) should participate in temporal calculations.

EVALUATION ONLY:
  - Never writes to production stores (all source reads are mode=ro).
  - Does NOT modify burst-policy-v1/v2 parameters or thresholds
    (POLICY/PARAMS consumed verbatim through the production modules).
  - Does NOT migrate production eu.published_at values.
  - Time policies exist only inside isolated frozen snapshots.

Pipeline (run order):
    build    freeze eu + entity-mention graph into snapshot.sqlite (+manifest)
    arms     materialise per-arm DBs: a_current / b_exclude / c_recover /
             d_interval_end (published_at column carries each arm semantics)
    replay   sequential daily sweep calling PRODUCTION scoring code
             (ef.concept_discovery._stats_for/_is_emerging,
              ef.burst_policy_v2.evaluate) over each arm;
             --knowledge-as-of adds the bitemporal predicate
             recorded_time <= as_of (late arrivals cannot influence
             dates before their ingestion was recorded).

Usage:
    python scripts/temporal_time_policy.py build --run-id <id>
    python scripts/temporal_time_policy.py arms --run-id <id>
    python scripts/temporal_time_policy.py replay --run-id <id> [--knowledge-as-of]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CATALOG_DB = Path("P:/.data/yt-is/ef/catalog.sqlite")
TRANSCRIPTS_DB = Path("P:/.data/yt-is/transcripts.sqlite")
LOG_ROOT = REPO / ".logs" / "temporal-time-policy"

DISCORD_EPOCH_MS_OFFSET = 1420070400000

EU_COLS = ("eu_id", "media_kind", "video_id", "channel_id", "channel_title",
           "title", "lang", "source", "authority_ref", "content_hash",
           "captured_at", "published_at")

ARM_LIST = ("a_current", "b_exclude", "c_recover", "d_interval_end")

# Cold-original DHT archive roots; canonical-first order mirrors
# scripts/run_dht_ingest.py discovery.
DHT_ARCHIVE_DIRS = ("G:/backups/dht", "P:/.data/dht",
                    "P:/.data/yt-is/dht")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _chunked_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def decode_snowflake_ms(message_id: int | str) -> int:
    """Discord/Twitter snowflake id -> UTC epoch milliseconds."""
    return ((int(message_id) >> 22) + DISCORD_EPOCH_MS_OFFSET)


def ms_to_date(ms: int | str) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ms) / 1000.0))


# --------------------------------------------------------------------------
# Snapshot build
# --------------------------------------------------------------------------

def build_snapshot(run_id: str) -> Path:
    out_dir = LOG_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    snap_path = out_dir / "snapshot.sqlite"
    if snap_path.exists():
        raise SystemExit(f"snapshot exists: {snap_path} — pick a new run id")
    src = sqlite3.connect(f"file:{CATALOG_DB.as_posix()}?mode=ro", uri=True)
    src.execute("PRAGMA busy_timeout=30000")
    dst = sqlite3.connect(str(snap_path))
    cols = ",".join(EU_COLS)
    dst.execute(
        """CREATE TABLE eu (eu_id TEXT PRIMARY KEY, media_kind TEXT,
           video_id TEXT, channel_id TEXT, channel_title TEXT DEFAULT '',
           title TEXT DEFAULT '', lang TEXT DEFAULT '', source TEXT,
           authority_ref TEXT, content_hash TEXT DEFAULT '',
           captured_at TEXT, published_at TEXT)""")
    # stream instead of re-querying repeatedly
    dst.execute("""CREATE TABLE kg_nodes (node_id TEXT PRIMARY KEY,
                   label TEXT, kind TEXT)""")
    dst.execute("""CREATE TABLE kg_edges (src_id TEXT, dst_id TEXT,
                   relation TEXT)""")
    n_eu = 0
    cur = src.cursor()
    cur.arraysize = 20000
    cur.execute(f"SELECT {cols} FROM eu")
    ph = ",".join("?" for _ in EU_COLS)
    buf = []
    ins = f"INSERT OR IGNORE INTO eu ({cols}) VALUES ({ph})"
    while True:
        rows = cur.fetchmany(20000)
        if not rows:
            break
        dst.executemany(ins, [(r[0], r[1], r[2], r[3] or "", r[4] or "",
                               r[5] or "", r[6] or "", r[7],
                               r[8], r[9] or "", r[10] or "",
                               r[11] or "") for r in rows])
        n_eu += len(rows)
    dst.commit()
    del buf
    scur = src.cursor()
    scur.arraysize = 50000
    scur.execute(
        "SELECT node_id, label, kind FROM kg_nodes WHERE kind='entity'")
    nn = 0
    while True:
        rows = scur.fetchmany(50000)
        if not rows:
            break
        dst.executemany("INSERT OR REPLACE INTO kg_nodes VALUES (?,?,?)",
                        rows)
        nn += len(rows)
    ecur.arraysize = 100000
    ecur.execute(
        """SELECT m.src_id, m.dst_id, m.relation FROM kg_edges m
           WHERE m.relation='mentioned_in'
             AND m.src_id IN (SELECT node_id FROM kg_nodes)""")
    ne = 0
    while True:
        rows = ecur.fetchmany(100000)
        if not rows:
            break
        dst.executemany("INSERT INTO kg_edges VALUES (?,?,?)", rows)
        ne += len(rows)
    has_rec = src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND "
        "name='eu_time_recovery'").fetchone()
    nr = 0
    dst.execute("""CREATE TABLE IF NOT EXISTS eu_time_recovery (
        eu_id text primary key, valid_start text not null,
        valid_end text not null, method text not null,
        approx integer not null default 0,
        previous_published_at text not null default '',
        source_field text not null, basis text not null default '',
        migration_version integer not null default 1,
        migrated_at text not null default '')""")
    if has_rec:
        rcur = src.cursor()
        rcur.execute("SELECT * FROM eu_time_recovery")
        names = [d[0] for d in rcur.description]
        ph = ",".join("?" for _ in names)
        while True:
            rows = rcur.fetchmany(20000)
            if not rows:
                break
            dst.executemany(f"INSERT OR REPLACE INTO eu_time_recovery ({','.join(names)}) "
                            f"VALUES ({ph})", rows)
            nr += len(rows)
    dst.execute("CREATE INDEX idx_eu_aref ON eu(authority_ref)")
    dst.execute("CREATE INDEX idx_kge_src ON kg_edges(src_id)")
    dst.commit()
    counts = {"eu": n_eu, "kg_nodes": nn, "kg_edges": ne,
              "eu_time_recovery": nr}
    dst.close()
    manifest = {
        "kind": "temporal-eu-time-policy-snapshot",
        "version": 1,
        "agent": "zcode",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_catalog": str(CATALOG_DB),
        "rows": counts,
        "sha256": _chunked_sha256(snap_path),
        "note": ("Entity-mention subset (relation mentioned_in) + eu table "
                 "frozen from the live catalog; input set of "
                 "ef.concept_discovery._entity_observations."),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"snapshot": str(snap_path), **counts}))
    return snap_path


# --------------------------------------------------------------------------
# Recovery surface (mechanically reconstructed valid time)
# --------------------------------------------------------------------------

_ARTIVE_CONN_CACHE: dict[str, sqlite3.Connection | None] = {}
_DHT_DIR_FILES: dict[str, dict[str, Path]] | None = None


def _dht_dir_files() -> dict[str, dict[str, Path]]:
    """lowercased filename stem -> {dht file} per candidate root."""
    global _DHT_DIR_FILES
    if _DHT_DIR_FILES is not None:
        return _DHT_DIR_FILES
    out: dict[str, dict[str, Path]] = {}
    for d in DHT_ARCHIVE_DIRS:
        p = Path(d)
        if not p.is_dir():
            continue
        m = out.setdefault(str(p), {})
        for f in p.glob("*.dht"):
            m[f.stem.lower()] = f
    _DHT_DIR_FILES = out
    return out


def _open_archive_for_slug(slug: str) -> sqlite3.Connection | None:
    if slug in _ARTIVE_CONN_CACHE:
        return _ARTIVE_CONN_CACHE[slug]
    con = None
    cands = {slug.lower(),
             slug.lower().replace("_", " "),
             slug.lower().replace("_", "-"),
             slug.lower().replace("_", "")}
    for _root, files in _dht_dir_files().items():
        for want in cands:
            if want in files:
                try:
                    con = sqlite3.connect(
                        f"file:{files[want].as_posix()}?mode=ro", uri=True,
                        timeout=15.0)
                    con.execute("PRAGMA busy_timeout=15000")
                    tables = {r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"attachments"} <= tables:
                        con.close()
                        con = None
                        continue
                except sqlite3.Error:
                    con = None
                    continue
                break
        if con is not None:
            break
    _ARTIVE_CONN_CACHE[slug] = con
    return con


def _normalize_ms(v) -> int | None:
    """DHT stores message timestamps either as epoch-ms ints or ISO strings
    depending on export generation."""
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    s = str(v).strip() if v is not None else ""
    if s.isdigit():
        return int(s)
    if not s:
        return None
    from datetime import datetime
    for parser in (datetime.fromisoformat,):
        try:
            dt = parser(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                from datetime import timezone
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def _attachment_snowflake_from_url(url: str) -> int | None:
    """cdn.discordapp.com/attachments/<channel>/<snowflake>/<name> -> id."""
    if not url:
        return None
    segs = [s for s in url.split("/") if s]
    try:
        att = segs[-2] if "cdn.discordapp.com" in url else ""
        return int(att) if att.isdigit() else None
    except (IndexError, ValueError):
        return None


def recover_valid_dates(transcripts_conn: sqlite3.Connection) -> dict:
    """Recovered valid dates keyed by transcript_cache.cache_key (= EU
    authority_ref for connector sources).

    discord windows: metadata_json.first_ts/last_ts (DHT messages.timestamp,
      epoch-ms ints written at ingest; validated exact against snowflake
      decode of anchor ids in run 20260826T2205).
    dht-artifact: message_id snowflake decode."""
    et = transcripts_conn.cursor()
    out: dict[str, dict] = {}
    for ck, meta_json in et.execute(
            "SELECT cache_key, metadata_json FROM transcript_cache "
            "WHERE source='discord'"):
        try:
            m = json.loads(meta_json or "{}")
        except json.JSONDecodeError:
            continue
        ft, lt = m.get("first_ts"), m.get("last_ts")
        if ft in (None, ""):
            continue
        start = ms_to_date(ft)
        end = ms_to_date(lt) if lt else start
        out[ck] = {"valid_start": start, "valid_end": end,
                   "method": "dht_window_first_ts_ms_epoch",
                   "approx": False}
    for ck, meta_json in et.execute(
            "SELECT cache_key, metadata_json FROM transcript_cache "
            "WHERE source='dht-artifact'"):
        try:
            m0 = json.loads(meta_json or "{}")
        except json.JSONDecodeError:
            m0 = {}
        mid_stored = m0.get("message_id")
        if str(mid_stored or "").isdigit() and int(mid_stored) > 0:
            ts = ms_to_date(decode_snowflake_ms(int(mid_stored)))
            out[ck] = {"valid_start": ts, "valid_end": ts,
                       "method": "message_id_snowflake", "approx": False}
            continue
        # message_id unknown at extraction time (downloads-table path).
        # Source-of-truth candidates, best first:
        #   1. raw archive bridge attachments.normalized_url -> messages
        #      row timestamp (exact);
        #   2. the Discord CDN url's own attachment snowflake (= the upload/
        #      send moment of the attachment object; APPROXIMATE, sub-minute
        #      skew, far tighter than any temporal window used here).
        # NOTE: attachment_id in these rows is a SYNTHETIC sha256 prefix and
        # must never be snowflake-decoded.
        url = m0.get("url") or ""
        ms = None
        method, approx = "unrecoverable", True
        conn = _open_archive_for_slug(ck.split(":")[1] if ":" in ck else "")
        if conn is not None and url:
            try:
                row = conn.execute(
                    """SELECT ma.message_id, m.timestamp FROM attachments a
                       JOIN message_attachments ma ON ma.attachment_id =
                            a.attachment_id
                       JOIN messages m ON m.message_id = ma.message_id
                       WHERE a.normalized_url = ? LIMIT 1""",
                    (url,)).fetchone()
            except sqlite3.Error:
                row = None
            if row:
                ms_v = _normalize_ms(row[1]) or decode_snowflake_ms(row[0])
                ms, method, approx = ms_v, \
                    "raw_archive_bridge_message_time", False
        if ms is None:
            att_flake = _attachment_snowflake_from_url(url)
            if att_flake is not None:
                ms, method, approx = decode_snowflake_ms(att_flake), \
                    "url_attachment_snowflake", True
        if ms is None:
            continue
        out[ck] = {"valid_start": ms_to_date(ms),
                   "valid_end": ms_to_date(ms),
                   "method": method, "approx": approx}
    return out


# --------------------------------------------------------------------------
# Arm application
# --------------------------------------------------------------------------

def apply_arm(snapshot_path: Path, arm: str) -> Path:
    """Materialise an arm-specific DB from the frozen snapshot.

    Semantics:
      a_current       published/captured columns verbatim (production
                      COALESCE substitution stays in force).
      b_exclude       undated EUs cannot contribute obs dates at all
                      (both columns blanked in this bounded replay copy).
      c_recover       mechanically recovered valid date written into
                      published_at for discord/dht-artifact EUs.
      d_interval_end  interval RIGHT edge instead of left edge (same as C
                      where the interval collapses to one day)."""
    out_path = snapshot_path.parent / f"arm_{arm}.sqlite"
    out_path.write_bytes(snapshot_path.read_bytes())
    con = sqlite3.connect(str(out_path))
    try:
        if arm == "a_current":
            pass
        elif arm == "b_exclude":
            con.execute(
                "UPDATE eu SET published_at='', captured_at='' "
                "WHERE (published_at='' OR published_at IS NULL)")
        elif arm in ("c_recover", "d_interval_start", "d_interval_end"):
            col = {"c_recover": "valid_start",
                   "d_interval_start": "valid_start",
                   "d_interval_end": "valid_end"}[arm]
            rc = sqlite3.connect(f"file:{TRANSCRIPTS_DB}?mode=ro", uri=True)
            rec = recover_valid_dates(rc)
            rc.close()
            con.execute(
                "CREATE TEMP TABLE rec (authority_ref TEXT PRIMARY KEY,"
                " v TEXT NOT NULL)")
            con.executemany(
                "INSERT OR REPLACE INTO rec VALUES (?,?)",
                [(k, vv[col]) for k, vv in rec.items()])
            n = con.execute(
                """UPDATE eu SET published_at=(
                       SELECT v FROM rec WHERE rec.authority_ref=eu.authority_ref)
                   WHERE source IN ('discord','dht-artifact')
                     AND authority_ref IN (SELECT authority_ref FROM rec)"""
            ).rowcount
            con.commit()
            print(json.dumps({"arm": arm, "rows_recovered": n}))
        else:
            raise ValueError(arm)
        con.commit()
        dated = con.execute(
            "SELECT COUNT(*) FROM eu WHERE published_at != ''").fetchone()[0]
        undated = con.execute(
            "SELECT COUNT(*) FROM eu WHERE published_at = ''"
            " OR published_at IS NULL").fetchone()[0]
    finally:
        con.close()
    print(json.dumps({"arm": arm, "path": str(out_path),
                      "dated": dated, "undated": undated}))
    return out_path


# --------------------------------------------------------------------------
# Replay engine — production scoring code over arm snapshots
# --------------------------------------------------------------------------

CD_IMPORT_SHIM = {"used": False}


def _import_concept_discovery():
    """Import ef.concept_discovery providing the missing
    evidence_cluster_inventory name as an inert shim when absent.

    Live defect found during this diagnostic (2026-08-26):
    ef/concept_discovery.py imports evidence_cluster_inventory from
    ef/evidence_clusters.py, but that module exports evidence_clusters()
    instead — every scan_internal() invocation currently fails at import
    time. Fixing that file belongs to the Temporal Emergence lane; this
    shim exists ONLY so this counterfactual can execute the verbatim
    observation/scoring functions (_entity_observations-equivalent SQL,
    _stats_for, _is_emerging). The shim itself is never called by this
    module. Every emitted report records whether the shim was applied."""
    try:
        from ef import concept_discovery as cd  # noqa: F401
        return cd
    except ImportError:
        pass
    import ef.evidence_clusters as ec
    if not hasattr(ec, "evidence_cluster_inventory"):
        def _shim(*args, **kwargs):
            raise NotImplementedError(
                "evidence_cluster_inventory shimmed by "
                "scripts/temporal_time_policy.py; live export is "
                "ef.evidence_clusters.evidence_clusters")
        _shim.__ttp_import_shim__ = True
        ec.evidence_cluster_inventory = _shim
        CD_IMPORT_SHIM["used"] = True
    from ef import concept_discovery as cd  # noqa: F401
    return cd


# --------------------------------------------------------------------------

def load_observations(db_path: Path) -> dict[str, list[dict]]:
    """Exactly ef.concept_discovery._entity_observations minus the as_of
    cutoff (applied per evaluation date), plus recorded_time for the
    optional bitemporal filter."""
    cd = _import_concept_discovery()
    conn = cd._catalog_ro(db_path)
    inner = r"""
        SELECT m.src_id AS node_id, n.label AS label, eu.eu_id AS eu_id,
               eu.video_id AS video_id, eu.channel_id AS channel_id,
               eu.source AS source,
               substr(CASE
                        WHEN NULLIF(eu.published_at,'') IS NOT NULL
                          THEN eu.published_at
                        WHEN r.valid_start IS NOT NULL
                          THEN r.valid_start
                        ELSE ''
                      END, 1, 10) AS obs_date,
               substr(eu.captured_at,1,10) AS recorded_date
        FROM kg_edges m
        JOIN kg_nodes n ON n.node_id = m.src_id AND n.kind = 'entity'
        JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
        LEFT JOIN eu_time_recovery r ON r.eu_id = eu.eu_id
        WHERE m.relation = 'mentioned_in'
    """
    rows = conn.execute(
        f"SELECT * FROM ({inner}) WHERE obs_date != ''").fetchall()
    conn.close()
    ents: dict[str, list[dict]] = {}
    for r in rows:
        ents.setdefault(r["node_id"], []).append({
            "label": r["label"], "eu_id": r["eu_id"],
            "video_id": r["video_id"], "channel_id": r["channel_id"],
            "source": r["source"], "obs_date": r["obs_date"],
            "recorded_date": r["recorded_date"],
        })
    return ents


def sweep_dates(start="2026-05-01", end="2026-08-26") -> list[str]:
    """Weekly cadence after month starts/mids, daily for the last week."""
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    days = []
    while d0 <= d1:
        if d0 >= d1 - timedelta(days=8) or d0.day in (1, 15):
            days.append(d0.isoformat())
        d0 += timedelta(days=1)
    return sorted(set(days))


def replay_arm(db_path: Path, knowledge_as_of: bool = False,
               start="2026-05-01", end="2026-08-26") -> dict:
    cd = _import_concept_discovery()
    import ef.burst_policy_v2 as bp2

    ents_raw = load_observations(db_path)
    ents: dict[str, list[dict]] = {}
    late_by_capture = 0
    for nid, obs in ents_raw.items():
        kept = []
        for o in obs:
            if not knowledge_as_of or (o["recorded_date"] or "").strip():
                kept.append({k: o[k] for k in (
                    "label", "eu_id", "video_id", "channel_id",
                    "source", "obs_date", "recorded_date")})
            else:
                late_by_capture += 1
        if kept:
            ents[nid] = kept

    mentions_total = sum(len(v) for v in ents.values())
    latest_valid_vs_recorded_gap = sum(
        1 for obs in ents.values() for o in obs
        if o["obs_date"] != "" and (o["recorded_date"] or "") != ""
        and o["obs_date"] < o["recorded_date"])

    prev_evals: dict[str, list[dict]] = {}
    timeline = []

    for ds in sweep_dates(start, end):
        as_of_d = date.fromisoformat(ds)
        ent_metrics = {}
        for nid, obs in ents.items():
            obs_le = []
            for o in obs:
                if o["obs_date"] > ds:
                    continue
                if knowledge_as_of:
                    rd = (o["recorded_date"] or "").strip()
                    if not rd or rd > ds:
                        continue
                obs_le.append(o)
            if not obs_le:
                continue
            stats = cd._stats_for(obs_le, as_of_d)
            emerging_v1 = cd._is_emerging(stats)
            pe = prev_evals.setdefault(nid, [])
            dec = bp2.evaluate(obs_le, as_of_d, pe)
            prev_evals[nid] = (list(pe) + [dec["eval"]])[-2:]
            ent_metrics[nid] = {
                "v1_emerging": bool(emerging_v1),
                "v1_recent_count": stats["recent_count"],
                "v1_baseline_count": stats["baseline_count"],
                "v1_smoothed_ratio": stats["smoothed_ratio"],
                "v1_first_seen": stats["first_seen"],
                "v2_positive": dec["positive"],
                "v2_candidate": dec["candidate"],
                "v2_promote": dec["promote"],
                "v2_posterior": dec["posterior"],
                "v2_k_recent": dec["k_recent"],
                "v2_k_base": dec["k_base"],
            }
        timeline.append({
            "as_of": ds,
            "entities_evaluated": len(ent_metrics),
            "v1_emerging_entities":
                sum(1 for m in ent_metrics.values() if m["v1_emerging"]),
            "v2_positive_entities":
                sum(1 for m in ent_metrics.values() if m["v2_positive"]),
            "v2_candidate_entities":
                sum(1 for m in ent_metrics.values() if m["v2_candidate"]),
            "v2_promote_events":
                sum(1 for m in ent_metrics.values() if m["v2_promote"]),
        })
    return {
        "arm_db": str(db_path),
        "knowledge_as_of": knowledge_as_of,
        "concept_discovery_import_shim_applied": CD_IMPORT_SHIM["used"],
        "entities_with_obs": len(ents),
        "mentions_total": mentions_total,
        "mentions_where_valid_before_recorded": latest_valid_vs_recorded_gap,
        "unrecorded_mentions_dropped_at_load": late_by_capture,
        "timeline": timeline,
        "final_day": timeline[-1]["as_of"] if timeline else None,
        "final_v1_emerging_entities": timeline[-1]["v1_emerging_entities"]
        if timeline else None,
        "final_v2_positive_entities": timeline[-1]["v2_positive_entities"]
        if timeline else None,
        # per-entity decision records of the LAST evaluation day
        "final_entity_metrics": ent_metrics,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["build", "arms", "replay"])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--knowledge-as-of", action="store_true",
                    help="enforce recorded_time<=as_of (bitemporal replay)")
    args = ap.parse_args(argv)
    out_dir = LOG_ROOT / args.run_id
    if args.cmd == "build":
        build_snapshot(args.run_id)
        return 0
    snap = out_dir / "snapshot.sqlite"
    if not snap.exists():
        raise SystemExit(f"no snapshot at {snap}; run build first")
    if args.cmd == "arms":
        for arm in ARM_LIST:
            apply_arm(snap, arm)
        return 0
    results = {}
    for arm in ARM_LIST:
        p = out_dir / f"arm_{arm}.sqlite"
        if not p.exists():
            p = apply_arm(snap, arm)
        results[arm] = replay_arm(p, knowledge_as_of=args.knowledge_as_of)
    suffix = "kasof" if args.knowledge_as_of else "wonly"
    (out_dir / f"replay_{suffix}.json").write_text(json.dumps(results, indent=2))
    summary = {arm: {k: r[k] for k in (
        "entities_with_obs", "mentions_total",
        "final_v1_emerging_entities", "final_v2_positive_entities")}
               for arm, r in results.items()}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
