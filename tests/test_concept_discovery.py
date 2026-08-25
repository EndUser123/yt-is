"""Tests for ef/concept_discovery.py — open-world internal discovery.

Synthetic SQLite fixture in tmp_path (never the production catalog).
Registry tables and EF tables coexist in ONE tmp DB, exactly like prod.
All concepts are fictional. No network, no provider.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ef import concept_discovery as cd  # noqa: E402
from ef import concept_registry as cr  # noqa: E402

SCHEMA = """
CREATE TABLE kg_nodes (node_id TEXT PRIMARY KEY, kind TEXT, label TEXT,
    weight REAL, meta_json TEXT);
CREATE TABLE kg_edges (src_id TEXT, dst_id TEXT, relation TEXT, weight REAL);
CREATE TABLE eu (eu_id TEXT PRIMARY KEY, video_id TEXT, channel_id TEXT,
    channel_title TEXT, title TEXT, source TEXT, captured_at TEXT,
    published_at TEXT);
CREATE TABLE chunk_clusters (chunk_id TEXT, point_id INTEGER, video_id TEXT,
    cluster_id INTEGER, assigned_at TEXT);
CREATE TABLE topic_clusters (cluster_id INTEGER PRIMARY KEY, label TEXT,
    member_count INTEGER, video_count INTEGER, top_terms TEXT,
    is_series INTEGER);
CREATE TABLE interests (interest_id TEXT PRIMARY KEY, name TEXT, kind TEXT);
CREATE TABLE evidence_links (link_id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_kind TEXT, src_id TEXT, dst_kind TEXT, dst_id TEXT, relation TEXT,
    strength REAL, created_at TEXT);
"""

AS_OF = "2026-08-25"


def build_db(tmp_path, name="cat.sqlite"):
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    cr.ensure_schema(conn)  # registry + EF tables coexist, like prod
    return conn


def entity(cur, node_id, label):
    cur.execute("INSERT INTO kg_nodes VALUES (?,?,?,?,NULL)",
                (node_id, "entity", label, 1.0))


def mention(cur, node_id, eu_id, channel, source, date_str):
    cur.execute("INSERT INTO eu VALUES (?,?,?,?,?,?,?,?)",
                (eu_id, f"vid_{eu_id}", channel, f"Channel {channel}",
                 f"Doc {eu_id}", source, None, date_str))
    cur.execute("INSERT INTO kg_edges VALUES (?,?,?,1.0)",
                (node_id, f"eu:{eu_id}", "mentioned_in"))


def cluster(cur, cid, label, channels=3, dates=("2026-07-01", "2026-07-15"),
            member_count=50):
    cur.execute("INSERT INTO topic_clusters VALUES (?,?,?,?,?,0)",
                (cid, label, member_count, 0, json.dumps([label])))
    n = 0
    for ch in range(channels):
        for d in dates:
            n += 1
            eu_id = f"{cid}:e{ch}:{d}"
            cur.execute("INSERT INTO eu VALUES (?,?,?,?,?,?,?,?)",
                        (eu_id, f"v_{cid}_{ch}", f"ch{ch}", f"Ch {ch}",
                         f"{label} doc {ch}", "ytdlp", None, d))
            cur.execute("INSERT INTO chunk_clusters VALUES (?,?,?,?,NULL)",
                        (f"c{cid}:{n}", n, f"v_{cid}_{ch}", cid))
    cur.execute("UPDATE topic_clusters SET video_count=? WHERE cluster_id=?",
                (n, cid))


def concept_row(conn, name, ctype):
    return conn.execute(
        "SELECT * FROM concepts WHERE concept_id = ?",
        (cr.concept_identity_id(ctype, name),),
    ).fetchone()


# ---------------------------------------------------------------- test 1
def test_new_entity_derived_without_predeclaration(tmp_path):
    conn = build_db(tmp_path)
    cur = conn.cursor()
    entity(cur, "ent:Quiblix Engine", "Quiblix Engine")
    for i, d in enumerate(("2026-08-20", "2026-08-21", "2026-08-22")):
        mention(cur, "ent:Quiblix Engine", f"q{i}", f"ch{i % 3}",
                "ytdlp" if i % 2 else "hackernews", d)
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    summary = cd.scan_internal(reg, catalog_path=tmp_path / "cat.sqlite",
                               as_of=AS_OF)
    row = concept_row(reg, "Quiblix Engine", "entity")
    assert row is not None, "concept must be derived from kg_nodes, not predeclared"
    assert row["lifecycle_state"] == "candidate"  # 3 recent < min_recent_count 4
    assert summary["entities_scanned"] == 1
    reg.close()


# ---------------------------------------------------------------- test 2
def test_burst_floors_and_emerging(tmp_path):
    conn = build_db(tmp_path)
    cur = conn.cursor()
    # weak: 1 old + 2 recent, single channel -> candidate only
    entity(cur, "ent:Flimworm", "Flimworm")
    mention(cur, "ent:Flimworm", "f0", "chA", "ytdlp", "2026-06-01")
    mention(cur, "ent:Flimworm", "f1", "chA", "ytdlp", "2026-08-20")
    mention(cur, "ent:Flimworm", "f2", "chA", "ytdlp", "2026-08-21")
    # strong: 5 recent across 3 channels / 2 source types, 1 baseline
    entity(cur, "ent:Zarnit Scope", "Zarnit Scope")
    mention(cur, "ent:Zarnit Scope", "z0", "ch1", "ytdlp", "2026-06-01")
    for i in range(5):
        mention(cur, "ent:Zarnit Scope", f"z{i + 1}", f"ch{i % 3}",
                "hackernews" if i % 2 else "ytdlp", f"2026-08-2{i}")
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    cd.scan_internal(reg, catalog_path=tmp_path / "cat.sqlite", as_of=AS_OF)
    weak = concept_row(reg, "Flimworm", "entity")
    strong = concept_row(reg, "Zarnit Scope", "entity")
    assert weak["lifecycle_state"] == "candidate"
    assert strong["lifecycle_state"] == "emerging"
    assert strong["world_signal_score"] > weak["world_signal_score"]
    ep = cr.active_episode(reg, strong["concept_id"])
    assert ep is not None and ep["state"] == "active"
    reg.close()


# ---------------------------------------------------------------- test 3
def test_source_diversity_beats_single_channel_volume(tmp_path):
    conn = build_db(tmp_path)
    cur = conn.cursor()
    # 50 recent mentions from ONE channel/source: huge count, no diversity
    entity(cur, "ent:Blorpticon", "Blorpticon")
    for i in range(50):
        mention(cur, "ent:Blorpticon", f"b{i}", "chA", "ytdlp", f"2026-08-2{i % 5}")
    # 6 mentions, 3 channels, 2 source types
    entity(cur, "ent:Vexol Primer", "Vexol Primer")
    for i in range(6):
        mention(cur, "ent:Vexol Primer", f"v{i}", f"ch{i % 3}",
                "hackernews" if i % 2 else "ytdlp", f"2026-08-2{i % 5}")
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    cd.scan_internal(reg, catalog_path=tmp_path / "cat.sqlite", as_of=AS_OF)
    loud = concept_row(reg, "Blorpticon", "entity")
    diverse = concept_row(reg, "Vexol Primer", "entity")
    assert loud["lifecycle_state"] == "candidate", \
        "50 mentions from one channel must NOT reach emerging"
    assert diverse["lifecycle_state"] == "emerging"
    reg.close()


# ---------------------------------------------------------------- test 4
def test_lifecycle_chain_candidate_emerging_cooling_dormant(tmp_path):
    conn = build_db(tmp_path)
    cur = conn.cursor()
    entity(cur, "ent:Gromfest", "Gromfest")
    for i in range(5):
        mention(cur, "ent:Gromfest", f"g{i}", f"ch{i % 3}",
                "hackernews" if i % 2 else "ytdlp", f"2026-08-2{i}")
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    catpath = tmp_path / "cat.sqlite"
    s1 = cd.scan_internal(reg, catalog_path=catpath, as_of=AS_OF)
    assert s1["emerging"] == 1
    cid = cr.concept_identity_id("entity", "Gromfest")
    first_seen_1 = concept_row(reg, "Gromfest", "entity")["first_seen"]
    # later scan, no new evidence -> cooling
    s2 = cd.scan_internal(reg, catalog_path=catpath, as_of="2026-10-05")
    assert s2["cooling"] == 1
    assert concept_row(reg, "Gromfest", "entity")["lifecycle_state"] == "cooling"
    assert cr.active_episode(reg, cid) is None
    # later still, no activity for > 2x recent window -> dormant
    s3 = cd.scan_internal(reg, catalog_path=catpath, as_of="2026-12-01")
    assert s3["dormant"] == 1
    row = concept_row(reg, "Gromfest", "entity")
    assert row["lifecycle_state"] == "dormant"
    assert row["first_seen"] == first_seen_1  # preserved after dormancy
    assert reg.execute(
        "SELECT COUNT(*) FROM trend_episodes WHERE concept_id = ?", (cid,)
    ).fetchone()[0] >= 1  # episode rows preserved, never deleted
    reg.close()


# ---------------------------------------------------------------- test 5
def test_second_burst_opens_second_episode(tmp_path):
    conn = build_db(tmp_path)
    cur = conn.cursor()
    entity(cur, "ent:Hambler", "Hambler")
    for i in range(5):
        mention(cur, "ent:Hambler", f"h{i}", f"ch{i % 3}",
                "hackernews" if i % 2 else "ytdlp", f"2026-08-2{i}")
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    catpath = tmp_path / "cat.sqlite"
    cd.scan_internal(reg, catalog_path=catpath, as_of=AS_OF)
    cd.scan_internal(reg, catalog_path=catpath, as_of="2026-10-05")  # cooled
    # second burst far later
    conn2 = sqlite3.connect(catpath)
    for i in range(5):
        mention(conn2.cursor(), "ent:Hambler", f"h2{i}", f"ch{i % 3}",
                "hackernews" if i % 2 else "ytdlp", f"2026-12-1{i}")
    conn2.commit()
    conn2.close()
    cd.scan_internal(reg, catalog_path=catpath, as_of="2026-12-20")
    cid = cr.concept_identity_id("entity", "Hambler")
    eps = reg.execute(
        "SELECT * FROM trend_episodes WHERE concept_id = ? ORDER BY started_at",
        (cid,),
    ).fetchall()
    assert len(eps) == 2, "a second burst after cooling must open a second episode"
    assert eps[0]["state"] == "cooled" and eps[0]["started_at"] == "2026-08-20"
    assert eps[1]["state"] == "active" and eps[1]["started_at"] == "2026-12-10"
    reg.close()


# ---------------------------------------------------------------- test 6
def test_as_of_cutoff_blocks_post_cutoff_evidence(tmp_path):
    conn = build_db(tmp_path)
    cur = conn.cursor()
    entity(cur, "ent:Skarnival", "Skarnival")
    for d in ("2026-01-01", "2026-02-01", "2026-03-01"):
        mention(cur, "ent:Skarnival", f"s{d}", "chOld", "ytdlp", d)
    for d in ("2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24"):
        mention(cur, "ent:Skarnival", f"s{d}", "chNew", "hackernews", d)
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    catpath = tmp_path / "cat.sqlite"
    early = cd.scan_internal(reg, catalog_path=catpath, as_of="2026-08-01")
    assert concept_row(reg, "Skarnival", "entity") is None, \
        "pre-cutoff scan must see no burst evidence"
    assert early["emerging"] == 0 and early["candidates"] == 0
    # no observation recorded from post-cutoff dates
    bad = reg.execute(
        "SELECT COUNT(*) FROM concept_observations WHERE observed_at > '2026-08-01'"
    ).fetchone()[0]
    assert bad == 0
    late = cd.scan_internal(reg, catalog_path=catpath, as_of="2026-08-25")
    assert late["candidates"] + late["emerging"] == 1
    row = concept_row(reg, "Skarnival", "entity")
    assert row is not None and row["first_seen"] == "2026-01-01"
    reg.close()


# ---------------------------------------------------------------- test 7
def test_idempotent_double_scan(tmp_path):
    conn = build_db(tmp_path)
    cur = conn.cursor()
    entity(cur, "ent:Twimlox", "Twimlox")
    for i in range(5):
        mention(cur, "ent:Twimlox", f"t{i}", f"ch{i % 3}",
                "hackernews" if i % 2 else "ytdlp", f"2026-08-2{i}")
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    catpath = tmp_path / "cat.sqlite"
    s1 = cd.scan_internal(reg, catalog_path=catpath, as_of=AS_OF)
    s2 = cd.scan_internal(reg, catalog_path=catpath, as_of=AS_OF)
    for table in ("concepts", "concept_observations", "trend_episodes"):
        n1 = reg.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n1 > 0 or table == "trend_episodes"
    counts = {
        t: reg.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("concepts", "concept_observations", "trend_episodes",
                  "concept_interest_links")
    }
    cd.scan_internal(reg, catalog_path=catpath, as_of=AS_OF)
    counts_again = {
        t: reg.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("concepts", "concept_observations", "trend_episodes",
                  "concept_interest_links")
    }
    assert counts == counts_again, "identical scan must not duplicate rows"
    assert {k: s1[k] for k in s1 if k != "runtime_s"} == \
           {k: s2[k] for k in s2 if k != "runtime_s"}
    reg.close()


# ---------------------------------------------------------------- test 8
def test_cluster_signal_topic_candidate(tmp_path):
    conn = build_db(tmp_path)
    cluster(conn.cursor(), 4242, "Zorblat Framework",
            channels=3, dates=("2026-07-05", "2026-07-20"))
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    summary = cd.scan_internal(reg, catalog_path=tmp_path / "cat.sqlite",
                               as_of=AS_OF)
    assert summary["cluster_candidates"] == 1
    row = concept_row(reg, "Zorblat Framework", "topic_cluster")
    assert row is not None and row["lifecycle_state"] == "candidate"
    meta = json.loads(row["metadata_json"])
    assert meta["cluster_id"] == 4242  # identity is the LABEL, id is metadata
    assert meta["evidence_signature"]
    reg.close()


# ---------------------------------------------------------------- test 9
def test_personal_relevance_shared_cluster(tmp_path):
    conn = build_db(tmp_path)
    cur = conn.cursor()
    cluster(cur, 777, "Nalproverb Studies", channels=3,
            dates=("2026-07-05", "2026-07-20"))
    cur.execute("INSERT INTO interests VALUES ('int_nal','Nalproverb studies','theme')")
    cur.execute(
        "INSERT INTO evidence_links (src_kind,src_id,dst_kind,dst_id,relation,"
        "strength,created_at) VALUES ('evidence_cluster','777','interest',"
        "'int_nal','supports',1.0,'2026-08-01')")
    conn.commit()
    conn.close()
    reg = cr.connect(tmp_path / "cat.sqlite")
    cd.scan_internal(reg, catalog_path=tmp_path / "cat.sqlite", as_of=AS_OF)
    cid = cr.concept_identity_id("topic_cluster", "Nalproverb Studies")
    link = reg.execute(
        "SELECT * FROM concept_interest_links WHERE concept_id = ?", (cid,)
    ).fetchone()
    # Registry layout: relation holds 'relevant_to', method holds the
    # linkage method (fixed 2026-08-24 from an earlier swapped insert).
    assert link is not None and link["relation"] == "relevant_to"
    assert link["method"] == "shared_cluster"
    assert json.loads(link["provenance_json"])["cluster_id"] == 777
    row = concept_row(reg, "Nalproverb Studies", "topic_cluster")
    assert row["user_relationship"] == "adjacent"
    assert row["personal_relevance_score"] is None  # never fabricated

    # same fixture WITHOUT evidence_links -> no link, unknown relationship
    conn2 = build_db(tmp_path, name="cat2.sqlite")
    cluster(conn2.cursor(), 777, "Nalproverb Studies", channels=3,
            dates=("2026-07-05", "2026-07-20"))
    conn2.commit()
    conn2.close()
    reg2 = cr.connect(tmp_path / "cat2.sqlite")
    cd.scan_internal(reg2, catalog_path=tmp_path / "cat2.sqlite", as_of=AS_OF)
    cid2 = cr.concept_identity_id("topic_cluster", "Nalproverb Studies")
    assert reg2.execute(
        "SELECT COUNT(*) FROM concept_interest_links WHERE concept_id = ?",
        (cid2,),
    ).fetchone()[0] == 0
    row2 = concept_row(reg2, "Nalproverb Studies", "topic_cluster")
    assert row2["user_relationship"] == "unknown"
    assert row2["personal_relevance_score"] is None
    reg2.close()
    reg.close()
