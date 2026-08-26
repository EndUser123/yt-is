"""Build the v1 knowledge graph data layer inside the Evidence Fabric catalog.

DATA LAYER ONLY: this is the cross-source synthesis substrate. It writes two
tables into the existing catalog (P:/.data/yt-is/ef/catalog.sqlite) and adds
no service routes — ef/warm_query_service.py and all HTTP code are untouched.
A later service layer can expose kg_nodes/kg_edges when needed.

Tables written (idempotent full rebuild inside one BEGIN IMMEDIATE txn):
    kg_nodes(node_id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT,
             weight REAL DEFAULT 0, meta_json TEXT)
    kg_edges(src_id TEXT NOT NULL, dst_id TEXT NOT NULL, relation TEXT NOT NULL,
             weight REAL DEFAULT 0, PRIMARY KEY(src_id, dst_id, relation))

Schema reality vs the original plan (verified via PRAGMA table_info
2026-08-22, and scripts/extract_entities.py):
  * entity_corpus is (entity, label, chunk_count, updated_at) — a corpus-wide
    FTS chunk count per entity, NOT per-eu mention rows. Per-eu 'mentioned_in'
    edges are therefore derived exactly the way extract_entities.refresh_counts
    derives corpus counts: FTS5 phrase matches in fts5.sqlite joined through
    catalog chunk(chunk_id -> eu_id). Edge weight = matched chunks in that eu;
    edges with fewer than 2 matches are skipped to keep the graph small.
  * 'in_channel' and 'mentioned_in' edges need eu endpoints, so eu nodes
    (kind 'eu', id "eu:<eu_id>") exist — scoped to eus touched by at least one
    surviving 'mentioned_in' edge. Untouched eus would be dangling bulk.
  * Entities present in `entities` but not in `entity_corpus` have no
    corpus-wide count and would be isolated nodes; they are excluded.

Node kinds and edge relations:
    ent:<name>  kind 'entity'  weight = entity_corpus.chunk_count
                label = entity name, meta_json {"type": PERSON/ORG/...}
                plus an "evidence" audit block (see below)
                (entity_corpus.label is the TYPE — putting it in kg_nodes.label
                collapses 388 entities into ~6 identical display labels)
    chan:<id>   kind 'channel' label = channel_title, weight = eu doc count
    src:<name>  kind 'source'  weight = eu doc count
    eu:<eu_id>  kind 'eu'      weight = 0 (entity-touched eus only)
    mentioned_in  ent -> eu    weight = FTS chunk matches in that eu (>= 2)
    in_channel    eu  -> chan  weight = 1
    of_source     chan -> src  weight = 1

Evidence-backed entity admission (E1, concept-quality audit follow-up):
an entity KG node exists ONLY if at least one qualifying EU supports it,
i.e. the FTS staging produced >= 1 mentioned_in row for that name. Entities
that qualify corpus-wide but have zero qualifying EUs are NOT admitted —
this removes the orphan-node class (nodes whose only claim was an LLM
self-reported mention count). Admission is an invariant, not a popularity
threshold: no >1-EU floor is applied.

Independent-publisher accounting (AUDIT FEATURE ONLY — never a gate):
meta_json["evidence"] on entity nodes carries:
    distinct_eu          qualifying supporting EUs
    distinct_publishers  distinct publisher identities among them
    publishers_known     same, excluding UNKNOWN identities
publisher identity semantics (documented, not invented):
    discord            -> "disc_guild:<guild_name>" (channel_title holds the
                          guild/server; one server may own many channels)
    hackernews         -> UNKNOWN ("hn" is one aggregator field)
    newsletter         -> UNKNOWN (channel_id is empty)
    everything else    -> channel_id verbatim; YouTube-class acquisition
                          modalities (notebooklm/ytdlp/selenium/whisper)
                          share the UC channel_id, so modality ≠ publisher
UNKNOWN identity is stored explicitly, never fabricated into independence.

Concurrency: the catalog is shared and WAL, with other services writing
concurrently. The expensive FTS staging runs on the write connection in temp
tables BEFORE the write transaction, so BEGIN IMMEDIATE is held only for the
bulk INSERT phase. On 'database is locked'/'busy' the write transaction is
rolled back and retried once after a 30s sleep; a second failure aborts the
run with a clear message. The rebuild is a single transaction, so an abort
never leaves partial kg_* tables.

Usage:
    python scripts/build_knowledge_graph.py             # rebuild + receipt
    python scripts/build_knowledge_graph.py --dry-run   # planned counts only
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
FTS = Path("P:/.data/yt-is/ef/fts5.sqlite")
MIN_EDGE_WEIGHT = 2  # skip mentioned_in edges with fewer matched chunks
UNKNOWN_PUBLISHER = "__UNKNOWN__"
LOCK_RETRY_SLEEP_S = 30.0

CREATE_NODES = """
CREATE TABLE kg_nodes (
    node_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT,
    weight REAL DEFAULT 0,
    meta_json TEXT
)
"""
CREATE_EDGES = """
CREATE TABLE kg_edges (
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL DEFAULT 0,
    PRIMARY KEY(src_id, dst_id, relation)
)
"""


class WriteLockAbort(RuntimeError):
    """The write transaction failed twice with 'database is locked'."""


def _connect_rw(catalog: Path) -> sqlite3.Connection:
    """Open the catalog read-write with the shared-DB lock contract."""
    uri = f"file:{catalog.as_posix()}?mode=rw"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=30000")
    # Big page cache: staging does ~1M PK dives into the chunk b-tree.
    conn.execute("PRAGMA cache_size=-262144")
    return conn


def publisher_identity(source, channel_id, channel_title):
    """Map an EU to its independent-publisher identity.

    Documented semantics (see module docstring): discord collapses to the
    guild/server, aggregator sources with no real per-publisher field are
    UNKNOWN, and YouTube-class acquisition modalities share the UC channel
    id so that modality never masquerades as publisher diversity.
    """
    cid = (channel_id or "").strip()
    if source == "discord":
        guild = (channel_title or "").strip()
        return f"disc_guild:{guild}" if guild else UNKNOWN_PUBLISHER
    if source in ("hackernews", "newsletter") or not cid:
        return UNKNOWN_PUBLISHER
    return cid


def _stage_publisher_map(conn: sqlite3.Connection) -> None:
    """Fill TEMP pub_map(eu_id, pub_id) for eus touched by staging."""
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS pub_map ("
        "eu_id TEXT PRIMARY KEY, pub_id TEXT NOT NULL)")
    conn.execute("DELETE FROM pub_map")
    rows = conn.execute(
        "SELECT DISTINCT s.eu_id, eu.source, eu.channel_id, eu.channel_title "
        "FROM ent_eu s JOIN main.eu eu ON eu.eu_id = s.eu_id").fetchall()
    conn.executemany(
        "INSERT OR REPLACE INTO pub_map VALUES (?, ?)",
        [(r[0], publisher_identity(r[1], r[2], r[3])) for r in rows])


def _stage_entity_pub(conn: sqlite3.Connection) -> None:
    """Aggregate per-entity audit counts from ent_eu x pub_map."""
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS entity_pub ("
        "entity TEXT PRIMARY KEY, distinct_eu INTEGER NOT NULL, "
        "distinct_publishers INTEGER NOT NULL, publishers_known INTEGER NOT NULL)")
    conn.execute("DELETE FROM entity_pub")
    conn.execute(
        "INSERT INTO entity_pub "
        "SELECT s.entity, COUNT(DISTINCT s.eu_id), "
        "       COUNT(DISTINCT p.pub_id), "
        "       COUNT(DISTINCT CASE WHEN p.pub_id <> ? THEN p.pub_id END) "
        "FROM ent_eu s JOIN pub_map p ON p.eu_id = s.eu_id "
        "GROUP BY s.entity", (UNKNOWN_PUBLISHER,))


def _fts_phrase(name: str) -> str | None:
    """FTS5 phrase for an entity name, or None if unmatchable.

    Same quoting policy as extract_entities.refresh_counts: wrap in double
    quotes; embedded quotes are stripped rather than breaking FTS syntax.
    """
    phrase = name.replace('"', " ").strip()
    if not phrase:
        return None
    return f'"{phrase}"'


def _stage_entity_edges(conn: sqlite3.Connection, fts: Path) -> int:
    """Fill temp table ent_eu(entity, eu_id, hits) from FTS matches.

    Reads fts5.sqlite (attached read-only) and catalog chunk/eu; writes only
    TEMP schema, so no main-database write lock is taken here.
    """
    conn.execute(
        "ATTACH DATABASE ? AS fts",
        (f"file:{fts.as_posix()}?mode=ro",))
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS ent_eu ("
        "entity TEXT NOT NULL, eu_id TEXT NOT NULL, hits INTEGER NOT NULL, "
        "PRIMARY KEY(entity, eu_id))")
    conn.execute("DELETE FROM ent_eu")
    names = [r[0] for r in conn.execute("SELECT entity FROM entity_corpus")]
    staged = 0
    total = len(names)
    for i, name in enumerate(names, 1):
        phrase = _fts_phrase(name)
        if phrase is None:
            continue
        try:
            conn.execute(
                "INSERT OR REPLACE INTO ent_eu "
                "SELECT ?, c.eu_id, COUNT(*) "
                "FROM fts.chunks f JOIN main.chunk c ON c.chunk_id = f.chunk_id "
                "WHERE f.chunks MATCH ? "
                "GROUP BY c.eu_id HAVING COUNT(*) >= ?",
                (name, phrase, MIN_EDGE_WEIGHT))
        except sqlite3.OperationalError:
            continue  # FTS-hostile name; same skip policy as refresh_counts
        staged += 1
        if i % 50 == 0:
            print(f"  staged {i}/{total} entities", flush=True)
    conn.execute("DETACH DATABASE fts")
    _stage_publisher_map(conn)
    _stage_entity_pub(conn)
    return staged


def _plan_counts(conn: sqlite3.Connection) -> dict:
    """Planned node/edge counts from SELECTs over staging + source tables."""
    valid_chan = "channel_id IS NOT NULL AND channel_id <> ''"
    # Evidence-backed admission: only staged entities become nodes.
    ent = conn.execute("SELECT COUNT(*) FROM entity_pub").fetchone()[0]
    chan = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM eu WHERE {valid_chan} "
        "GROUP BY channel_id)").fetchone()[0]
    src = conn.execute(
        "SELECT COUNT(DISTINCT source) FROM eu "
        "WHERE source IS NOT NULL AND source <> ''").fetchone()[0]
    eu_touched = conn.execute(
        "SELECT COUNT(DISTINCT eu_id) FROM ent_eu").fetchone()[0]
    mentioned = conn.execute("SELECT COUNT(*) FROM ent_eu").fetchone()[0]
    in_channel = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT s.eu_id FROM ent_eu s "
        f"JOIN eu ON eu.eu_id = s.eu_id WHERE {valid_chan})").fetchone()[0]
    of_source = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT 1 FROM eu WHERE {valid_chan} "
        "AND source IS NOT NULL AND source <> '' "
        "GROUP BY channel_id, source)").fetchone()[0]
    return {
        "nodes": ent + chan + src + eu_touched,
        "by_kind": {"entity": ent, "channel": chan, "source": src,
                    "eu": eu_touched},
        "edges": mentioned + in_channel + of_source,
        "by_relation": {"mentioned_in": mentioned, "in_channel": in_channel,
                        "of_source": of_source},
    }


def _write_phase(conn: sqlite3.Connection) -> None:
    """DROP + CREATE + populate both kg_* tables in one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DROP TABLE IF EXISTS kg_edges")
        conn.execute("DROP TABLE IF EXISTS kg_nodes")
        conn.execute(CREATE_NODES)
        conn.execute(CREATE_EDGES)
        # Entity nodes: evidence-backed admission only (entity_pub holds
        # exactly the names with >= 1 qualifying EU). weight = corpus
        # chunk_count when known; meta carries the type plus the audit
        # evidence block (distinct EU count and independent-publisher
        # counts — accounting only, never used as a gate).
        conn.execute(
            "INSERT INTO kg_nodes (node_id, kind, label, weight, meta_json) "
            "SELECT 'ent:' || ep.entity, 'entity', ep.entity, "
            "       COALESCE(ec.chunk_count, 0), "
            "       '{\"type\": \"' || COALESCE(ec.label, 'CONCEPT') || "
            "'\", \"evidence\": {\"distinct_eu\": ' || ep.distinct_eu || "
            "', \"distinct_publishers\": ' || ep.distinct_publishers || "
            "', \"publishers_known\": ' || ep.publishers_known || '}}' "
            "FROM entity_pub ep "
            "LEFT JOIN entity_corpus ec ON ec.entity = ep.entity")
        # Channel nodes: weight = doc (eu) count; label = channel_title.
        conn.execute(
            "INSERT INTO kg_nodes (node_id, kind, label, weight, meta_json) "
            "SELECT 'chan:' || channel_id, 'channel', MAX(channel_title), "
            "COUNT(*), '{}' FROM eu "
            "WHERE channel_id IS NOT NULL AND channel_id <> '' "
            "GROUP BY channel_id")
        # Source nodes: weight = doc count.
        conn.execute(
            "INSERT INTO kg_nodes (node_id, kind, label, weight, meta_json) "
            "SELECT 'src:' || source, 'source', NULL, COUNT(*), '{}' FROM eu "
            "WHERE source IS NOT NULL AND source <> '' GROUP BY source")
        # eu nodes: only eus touched by a surviving mentioned_in edge.
        conn.execute(
            "INSERT INTO kg_nodes (node_id, kind, label, weight, meta_json) "
            "SELECT DISTINCT 'eu:' || eu_id, 'eu', NULL, 0, '{}' FROM ent_eu")
        # entity -> eu mention edges (already filtered to hits >= 2 in staging).
        conn.execute(
            "INSERT INTO kg_edges (src_id, dst_id, relation, weight) "
            "SELECT 'ent:' || entity, 'eu:' || eu_id, 'mentioned_in', hits "
            "FROM ent_eu")
        # eu -> channel edges, for touched eus with a resolvable channel.
        conn.execute(
            "INSERT INTO kg_edges (src_id, dst_id, relation, weight) "
            "SELECT DISTINCT 'eu:' || s.eu_id, 'chan:' || eu.channel_id, "
            "'in_channel', 1 FROM ent_eu s "
            "JOIN eu ON eu.eu_id = s.eu_id "
            "WHERE eu.channel_id IS NOT NULL AND eu.channel_id <> ''")
        # channel -> source edges, derived from eu rows of that channel.
        conn.execute(
            "INSERT INTO kg_edges (src_id, dst_id, relation, weight) "
            "SELECT 'chan:' || channel_id, 'src:' || source, 'of_source', 1 "
            "FROM eu WHERE channel_id IS NOT NULL AND channel_id <> '' "
            "AND source IS NOT NULL AND source <> '' "
            "GROUP BY channel_id, source")
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def _write_with_retry(conn: sqlite3.Connection) -> None:
    """Run the write phase, retrying once on lock after a 30s sleep."""
    for attempt in (1, 2):
        try:
            _write_phase(conn)
            return
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            if attempt == 2:
                raise WriteLockAbort(
                    "kg rebuild aborted: catalog write transaction failed "
                    "twice with 'database is locked' "
                    f"(second error: {exc})") from exc
            print("database is locked; sleeping 30s before one retry",
                  flush=True)
            time.sleep(LOCK_RETRY_SLEEP_S)


def _receipt(conn: sqlite3.Connection) -> dict:
    by_kind = dict(conn.execute(
        "SELECT kind, COUNT(*) FROM kg_nodes GROUP BY kind"))
    by_relation = dict(conn.execute(
        "SELECT relation, COUNT(*) FROM kg_edges GROUP BY relation"))
    return {
        "nodes": sum(by_kind.values()),
        "by_kind": by_kind,
        "edges": sum(by_relation.values()),
        "by_relation": by_relation,
    }


def build_knowledge_graph(catalog: Path, fts: Path,
                          dry_run: bool = False) -> dict:
    """Rebuild kg_nodes/kg_edges in `catalog`; return a count receipt."""
    t0 = time.monotonic()
    conn = _connect_rw(catalog)
    try:
        _stage_entity_edges(conn, fts)
        plan = _plan_counts(conn)
        if dry_run:
            plan = {"dry_run": True, **plan}
            plan["seconds"] = round(time.monotonic() - t0, 1)
            return plan
        _write_with_retry(conn)
        receipt = _receipt(conn)
    finally:
        conn.close()
    receipt["seconds"] = round(time.monotonic() - t0, 1)
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the v1 knowledge graph data layer (kg_nodes/kg_edges) "
                    "in the EF catalog. Data layer only; no service routes.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print planned node/edge counts from SELECTs without writing")
    args = parser.parse_args(argv)
    try:
        receipt = build_knowledge_graph(CATALOG, FTS, dry_run=args.dry_run)
    except WriteLockAbort as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 2
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
