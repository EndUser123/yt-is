"""Tests for ef/evidence_clusters.py — inventory/hydration layering.

Synthetic SQLite fixture in tmp_path (never the production catalog):
60+ healthy eligible clusters plus series / member-floor / channel-floor
exclusion cases. Fictional topics only (distributed databases, compiler
optimization, gardening, astronomy).
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ef import evidence_clusters as ec  # noqa: E402

TOPICS = [
    "distributed databases", "compiler optimization", "gardening",
    "astronomy", "mushroom foraging", "mechanical keyboards",
    "urban cycling", "sourdough baking", "retro computing",
    "tide pooling", "bird migration", "street photography",
    "solar observing", "ham radio", "container gardening",
    "weather modeling", "flight simulation", "kayak repair",
    "bee keeping", "map projections",
]

SCHEMA = """
CREATE TABLE topic_clusters (cluster_id INTEGER PRIMARY KEY, label TEXT,
    member_count INTEGER, video_count INTEGER, top_terms TEXT,
    is_series INTEGER);
CREATE TABLE eu (eu_id TEXT PRIMARY KEY, video_id TEXT, channel_id TEXT,
    source TEXT, title TEXT, channel_title TEXT, published_at TEXT,
    captured_at TEXT);
CREATE TABLE chunk_clusters (cluster_id INTEGER, video_id TEXT);
CREATE TABLE kg_nodes (node_id TEXT PRIMARY KEY, label TEXT);
CREATE TABLE kg_edges (src_id TEXT, dst_id TEXT, relation TEXT);
"""

SERIES_ID = 9001
BELOW_MEMBER_ID = 9002
TWO_CHANNEL_ID = 9003
HEALTHY = list(range(1000, 1060))  # 60 healthy eligible clusters


def _seed_cluster(cur, cid, label, member_count, channels, sources,
                  months, is_series=0, terms=None):
    cur.execute(
        "INSERT INTO topic_clusters VALUES (?,?,?,?,?,?)",
        (cid, label, member_count, member_count,
         json.dumps(terms or [label, "evidence"]), is_series))
    n = 0
    for ch in range(channels):
        for m_i, month in enumerate(months):
            for k in range(2):
                n += 1
                eu_id = f"{cid}:e{ch}:{m_i}:{k}"
                vid = f"{cid}:v{ch}:{m_i}:{k}"
                src = sources[(ch + m_i + k) % len(sources)]
                title = (f"{label} deep dive part {ch}{m_i}{k} "
                         "extended edition")
                cur.execute(
                    "INSERT INTO eu VALUES (?,?,?,?,?,?,?,?)",
                    (eu_id, vid, f"chan-{cid}-{ch}", src, title,
                     f"Channel {label} {ch}",
                     f"{month}-1{n:02d}T00:00:00Z", None))
                cur.execute(
                    "INSERT INTO chunk_clusters VALUES (?,?)", (cid, vid))
    cur.execute("UPDATE topic_clusters SET video_count = ?"
                " WHERE cluster_id = ?", (n, cid))


def _seed_entities(cur):
    # Specific entity: only in cluster 1000, dv >= 3 -> kept.
    cur.execute("INSERT INTO kg_nodes VALUES ('n-spec','Raft Log Compaction')")
    # Diffuse entity: 5 videos in each of many clusters -> spread wide,
    # specificity < 0.08 and dv < 30 -> dropped from every packet.
    cur.execute("INSERT INTO kg_nodes VALUES ('n-diff','Consensus')")
    diff_videos = []
    for cid in HEALTHY[:20]:
        for m in range(4):
            diff_videos.append(f"{cid}:v0:{m}:0")
    rows = list(cur.execute("SELECT eu_id FROM eu"
                            " WHERE video_id LIKE '1000:v0:%'"))
    for (eu_id,) in rows[:5]:
        cur.execute("INSERT INTO kg_edges VALUES (?,?,?)",
                    ("n-spec", f"eu:{eu_id}", "mentioned_in"))
    for eu_id, vid in cur.execute(
            "SELECT eu_id, video_id FROM eu WHERE video_id IN (%s)"
            % ",".join("?" * len(diff_videos)), diff_videos).fetchall():
            cur.execute("INSERT INTO kg_edges VALUES ('n-diff', 'eu:'||?,"
                        " 'mentioned_in')", (eu_id,))


def build_catalog(path: Path, reverse_insert=False):
    """Fixture factory. Same logical data every call; reverse_insert
    changes only the row insertion order (order-stability check)."""
    if Path(path).exists():
        Path(path).unlink()
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    cur = conn.cursor()

    plans = []
    for i, cid in enumerate(HEALTHY):
        months = sorted({"2025-01", "2025-0%d" % (2 + i % 5),
                         "2025-06", "2024-1%d" % (i % 2)})
        sources = ["ytdlp"]
        if cid == 1000:
            sources = ["ytdlp", "notebooklm"]
        plans.append((cid, TOPICS[i % len(TOPICS)] + f" {i}",
                      40 + i, 3 + (i % 5), sources, months, 0))
    plans.append((SERIES_ID, "channel playlist echo", 400, 1,
                  ["ytdlp"], ["2025-03"], 1))
    plans.append((BELOW_MEMBER_ID, "tiny cluster", 10, 4,
                  ["ytdlp"], ["2025-03"], 0))
    plans.append((TWO_CHANNEL_ID, "narrow cluster", 50, 2,
                  ["ytdlp"], ["2025-03"], 0))

    if reverse_insert:
        plans = plans[::-1]
    for p in plans:
        _seed_cluster(cur, *p)
    _seed_entities(cur)
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture(scope="module")
def catalog_path(tmp_path_factory):
    return build_catalog(
        tmp_path_factory.mktemp("cat") / "catalog.sqlite")


def test_inventory_no_topn(catalog_path):
    inv = ec.evidence_cluster_inventory(catalog_path=catalog_path)
    ids = [e["cluster_id"] for e in inv["clusters"]]
    assert inv["eligible_count"] == 60
    assert len(inv["clusters"]) == 60
    assert ids == sorted(ids)
    assert SERIES_ID not in ids and BELOW_MEMBER_ID not in ids
    assert TWO_CHANNEL_ID not in ids


def test_inventory_exclusions(catalog_path):
    inv = ec.evidence_cluster_inventory(catalog_path=catalog_path)
    ex = inv["exclusions"]
    assert ex["series"] == 1
    assert ex["member_count_below_floor"] == 1
    assert ex["channels_below_floor"] == 1
    assert inv["total_semantic_non_series"] == 62
    assert inv["eligible_count"] == (
        inv["total_semantic_non_series"] - ex["member_count_below_floor"]
        - ex["channels_below_floor"])


def test_inventory_entry_shape(catalog_path):
    inv = ec.evidence_cluster_inventory(catalog_path=catalog_path)
    entry = next(e for e in inv["clusters"] if e["cluster_id"] == 1000)
    assert set(entry) == {
        "cluster_id", "label", "member_count", "video_count", "channels",
        "documents", "active_months", "first_month", "last_month", "phase",
        "sources", "terms", "evidence_signature"}
    assert entry["terms"] == ["distributed databases 0", "evidence"]
    assert len(entry["terms"]) <= 8
    # notebooklm must merge into youtube via SOURCE_LABELS
    labels = [s[0] for s in entry["sources"]]
    assert "youtube" in labels and "notebooklm" not in labels
    assert entry["first_month"] == "2024-10" or \
        entry["first_month"].startswith(("2024", "2025"))
    assert isinstance(entry["evidence_signature"], str)
    assert len(entry["evidence_signature"]) == 16
    assert entry["phase"] == "dormant"  # first 2024, months >= 3


def test_signature_deterministic(tmp_path):
    p1 = build_catalog(tmp_path / "a.sqlite")
    p2 = build_catalog(tmp_path / "b.sqlite")
    s1 = {e["cluster_id"]: e["evidence_signature"]
          for e in ec.evidence_cluster_inventory(catalog_path=p1)["clusters"]}
    s2 = {e["cluster_id"]: e["evidence_signature"]
          for e in ec.evidence_cluster_inventory(catalog_path=p2)["clusters"]}
    assert s1 == s2
    conn = sqlite3.connect(p2)
    conn.execute("UPDATE topic_clusters SET member_count = member_count + 7"
                 " WHERE cluster_id = 1005")
    conn.commit()
    conn.close()
    s3 = {e["cluster_id"]: e["evidence_signature"]
          for e in ec.evidence_cluster_inventory(catalog_path=p2)["clusters"]}
    assert s3[1005] != s1[1005]
    changed = {k for k in s1 if s1[k] != s3[k]}
    assert changed == {1005}


def test_inventory_order_stable(tmp_path):
    p1 = build_catalog(tmp_path / "o1.sqlite")
    p2 = build_catalog(tmp_path / "o2.sqlite", reverse_insert=True)
    a = ec.evidence_cluster_inventory(catalog_path=p1)["clusters"]
    b = ec.evidence_cluster_inventory(catalog_path=p2)["clusters"]
    assert a == b


def test_hydration_packets(catalog_path):
    pkts = ec.hydrate_evidence_clusters([1002, 1000, 1001],
                                        catalog_path=catalog_path)
    assert [p["cluster_id"] for p in pkts] == [1000, 1001, 1002]
    for p in pkts:
        assert set(p) == {
            "cluster_id", "label", "terms", "channels", "documents",
            "videos", "active_months", "first_month", "last_month",
            "phase", "sources", "entities", "representative"}
        assert p["representative"], "representative docs must be present"
        assert len(p["representative"]) <= 8
    p1000 = pkts[0]
    ent_names = [e["entity"] for e in p1000["entities"]]
    # diffuse entity: 5 videos spread over 20 clusters -> dropped
    assert "Consensus" not in ent_names
    # specific entity: only this cluster -> kept
    assert "Raft Log Compaction" in ent_names


def test_hydration_unknown_and_ineligible(catalog_path):
    with pytest.raises(ValueError, match="4242"):
        ec.hydrate_evidence_clusters([1000, 4242],
                                     catalog_path=catalog_path)
    with pytest.raises(ValueError, match="9001"):
        ec.hydrate_evidence_clusters([SERIES_ID],
                                     catalog_path=catalog_path)
    with pytest.raises(ValueError, match="9003"):
        ec.hydrate_evidence_clusters([TWO_CHANNEL_ID],
                                     catalog_path=catalog_path)
    with pytest.raises(ValueError, match="9002"):
        ec.hydrate_evidence_clusters([BELOW_MEMBER_ID],
                                     catalog_path=catalog_path)


def test_hydration_dedup(catalog_path):
    pkts = ec.hydrate_evidence_clusters(
        [1000, 1000, 1001, 1001, 1000], catalog_path=catalog_path)
    assert [p["cluster_id"] for p in pkts] == [1000, 1001]


def test_legacy_compat(catalog_path, monkeypatch):
    # legacy path has no catalog_path param: point CATALOG at fixture
    monkeypatch.setattr(ec, "CATALOG", Path(catalog_path))
    pkts = ec.evidence_clusters(min_member_count=40, top_clusters=5)
    assert len(pkts) <= 5
    keys = [(p["channels"], p["documents"]) for p in pkts]
    assert keys == sorted(keys, key=lambda t: (-t[0], -t[1]))
    full = ec.hydrate_evidence_clusters(
        [p["cluster_id"] for p in pkts], catalog_path=catalog_path)
    assert [set(p) for p in pkts] == [set(p) for p in full]


def test_hydrate_all_60(catalog_path):
    pkts = ec.hydrate_evidence_clusters(HEALTHY,
                                        catalog_path=catalog_path)
    assert len(pkts) == 60
    assert [p["cluster_id"] for p in pkts] == sorted(HEALTHY)


def test_readonly_connections(catalog_path, monkeypatch):
    real = sqlite3.connect

    def guarded(*a, **kw):
        uri = a[0] if a else kw.get("database", "")
        assert "mode=ro" in uri, f"non-read-only URI: {uri}"
        return real(*a, **kw)

    monkeypatch.setattr(ec.sqlite3, "connect", guarded)
    ec.evidence_cluster_inventory(catalog_path=catalog_path)
    ec.hydrate_evidence_clusters([1000], catalog_path=catalog_path)


def test_connections_are_closed_not_just_committed(catalog_path, tmp_path,
                                                   monkeypatch):
    """`with sqlite3.connect(...)` commits/rollbacks but never CLOSES; the
    module's entry points ran on connections left open to the GC. Every
    connection opened by inventory/hydration/coverage must be closed when
    the call returns."""
    import gc
    opened = []
    real_catalog = ec._catalog

    def spy(*a, **k):
        c = real_catalog(*a, **k)
        opened.append(c)
        return c

    monkeypatch.setattr(ec, "_catalog", spy)
    batch = tmp_path / "batch.sqlite"
    b = sqlite3.connect(batch)
    b.executescript("""
        CREATE TABLE channel_metadata (channel_id TEXT);
        CREATE TABLE channel_blocklist (channel_id TEXT);
        CREATE TABLE video_catalog (video_id TEXT);
        CREATE TABLE analysis_status (status TEXT);
    """)
    b.commit()
    b.close()
    monkeypatch.setattr(ec, "BATCH", batch)

    ec.evidence_cluster_inventory(catalog_path=catalog_path)
    ec.hydrate_evidence_clusters([1000], catalog_path=catalog_path)
    chain = ec.coverage_chain()
    assert chain["tracked_channels"] == 0  # batch fixture is queryable

    gc.collect()
    assert opened, "expected _catalog to be exercised"
    for c in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("select 1")     # closed connections refuse queries
