"""Tests for the knowledge graph build script (data layer only).

The build reads eu/chunk/entity_corpus from a temp catalog, derives per-eu
mention edges from a temp fts5.sqlite mirror (same shape the production
schema uses), and writes kg_nodes/kg_edges back into the same catalog file.
No LLM, no live services, no live catalog.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

from scripts.build_knowledge_graph import build_knowledge_graph


CATALOG_SCHEMA = textwrap.dedent("""
    CREATE TABLE eu (
        eu_id TEXT PRIMARY KEY,
        video_id TEXT NOT NULL,
        channel_id TEXT NOT NULL DEFAULT '',
        channel_title TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        source TEXT
    );
    CREATE TABLE chunk (
        chunk_id TEXT PRIMARY KEY,
        eu_id TEXT NOT NULL,
        start_char INTEGER NOT NULL,
        end_char INTEGER NOT NULL
    );
    CREATE TABLE entities (
        entity TEXT NOT NULL,
        label TEXT NOT NULL,
        cluster_id INTEGER NOT NULL,
        mentions INTEGER DEFAULT 1,
        extracted_at TEXT,
        PRIMARY KEY(entity, cluster_id)
    );
    CREATE TABLE entity_corpus (
        entity TEXT PRIMARY KEY,
        label TEXT,
        chunk_count INTEGER,
        updated_at TEXT
    );
""").strip()


def _build_catalog(tmp_path: Path) -> Path:
    """Minimal but faithful catalog seed.

    Channels: C1 (2 docs, youtube), C2 (1 doc, reddit); eu5 has an empty
    channel_id but source youtube. PyTorch matches 2 chunks in eu1, 1 in
    eu2 (filtered: <2), 2 in eu3, 2 in eu5. Kafka matches only 1 chunk
    anywhere (filtered). Bedrock has no FTS matches (node, no edges).
    'Lonely' exists only in `entities`, not entity_corpus (excluded).
    """
    cat = tmp_path / "catalog.sqlite"
    conn = sqlite3.connect(cat)
    conn.executescript(CATALOG_SCHEMA)
    conn.executemany(
        "INSERT INTO eu (eu_id, video_id, channel_id, channel_title, source) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("eu1", "v1", "C1", "Chan One", "youtube"),
            ("eu2", "v2", "C1", "Chan One", "youtube"),
            ("eu3", "v3", "C2", "Chan Two", "reddit"),
            ("eu5", "v5", "", "", "youtube"),
        ])
    conn.executemany(
        "INSERT INTO chunk (chunk_id, eu_id, start_char, end_char) "
        "VALUES (?, ?, 0, 10)",
        [
            ("a1", "eu1"), ("a2", "eu1"), ("d1", "eu1"),
            ("b1", "eu2"),
            ("c1", "eu3"), ("c2", "eu3"),
            ("f1", "eu5"), ("f2", "eu5"),
        ])
    conn.executemany(
        "INSERT INTO entities VALUES (?, ?, ?, ?, ?)",
        [("Lonely", "ORG", 9, 5, "2026-08-21T00:00:00Z")])
    conn.executemany(
        "INSERT INTO entity_corpus VALUES (?, ?, ?, ?)",
        [
            ("PyTorch", "TECH", 7, "2026-08-21T00:00:00Z"),
            ("Kafka", "CONCEPT", 1, "2026-08-21T00:00:00Z"),
            ("Bedrock", "PRODUCT", 0, "2026-08-21T00:00:00Z"),
        ])
    conn.commit()
    conn.close()
    return cat


def _build_fts(tmp_path: Path) -> Path:
    fts = tmp_path / "fts5.sqlite"
    conn = sqlite3.connect(fts)
    conn.execute(
        "CREATE VIRTUAL TABLE chunks USING fts5(text, chunk_id UNINDEXED)")
    conn.executemany(
        "INSERT INTO chunks(text, chunk_id) VALUES (?, ?)",
        [
            ("PyTorch rocks", "a1"),
            ("PyTorch again", "a2"),
            ("Kafka event", "d1"),
            ("PyTorch once", "b1"),
            ("PyTorch there", "c1"),
            ("PyTorch more", "c2"),
            ("PyTorch alpha", "f1"),
            ("PyTorch beta", "f2"),
        ])
    conn.commit()
    conn.close()
    return fts


def _run(tmp_path: Path, dry_run: bool = False) -> dict:
    cat = _build_catalog(tmp_path)
    fts = _build_fts(tmp_path)
    return build_knowledge_graph(cat, fts, dry_run=dry_run)


def _rows(tmp_path: Path, sql: str):
    conn = sqlite3.connect(tmp_path / "catalog.sqlite")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


EXPECTED_RECEIPT = {
    "nodes": 10,
    "by_kind": {"entity": 3, "channel": 2, "source": 2, "eu": 3},
    "edges": 7,
    "by_relation": {"mentioned_in": 3, "in_channel": 2, "of_source": 2},
}


def test_node_kinds_labels_and_weights(tmp_path):
    """Correct node kinds, labels, and weights for all four kinds."""
    _run(tmp_path)
    nodes = {r[0]: r[1:] for r in _rows(
        tmp_path, "SELECT node_id, kind, label, weight FROM kg_nodes")}
    assert set(nodes) == {
        "ent:PyTorch", "ent:Kafka", "ent:Bedrock",
        "chan:C1", "chan:C2", "src:youtube", "src:reddit",
        "eu:eu1", "eu:eu3", "eu:eu5"}
    # Entity nodes: weight = total corpus mentions, label = the entity
    # NAME (the type lives in meta_json, not label).
    assert nodes["ent:PyTorch"] == ("entity", "PyTorch", 7.0)
    assert nodes["ent:Bedrock"] == ("entity", "Bedrock", 0.0)
    # Channel nodes: label = title, weight = doc count.
    assert nodes["chan:C1"] == ("channel", "Chan One", 2.0)
    assert nodes["chan:C2"] == ("channel", "Chan Two", 1.0)
    # Source nodes: weight = doc count.
    assert nodes["src:youtube"] == ("source", None, 3.0)
    assert nodes["src:reddit"] == ("source", None, 1.0)
    # eu nodes: weight 0; eu2 excluded (only 1 PyTorch chunk -> no edge).
    assert nodes["eu:eu1"] == ("eu", None, 0.0)


def test_edges_and_low_count_filtering(tmp_path):
    """Edges use the specified relations; mention counts < 2 are excluded."""
    _run(tmp_path)
    edges = _rows(
        tmp_path,
        "SELECT src_id, dst_id, relation, weight FROM kg_edges "
        "WHERE relation = 'mentioned_in'")
    # Kafka (1 chunk match) has no edges; PyTorch keeps eu1/eu3/eu5 (2 hits
    # each) and drops eu2 (1 hit).
    assert sorted(edges) == [
        ("ent:PyTorch", "eu:eu1", "mentioned_in", 2.0),
        ("ent:PyTorch", "eu:eu3", "mentioned_in", 2.0),
        ("ent:PyTorch", "eu:eu5", "mentioned_in", 2.0),
    ]
    in_channel = _rows(
        tmp_path,
        "SELECT src_id, dst_id FROM kg_edges WHERE relation = 'in_channel'")
    # eu5 has an empty channel_id: touched, but no in_channel edge.
    assert sorted(in_channel) == [("eu:eu1", "chan:C1"),
                                  ("eu:eu3", "chan:C2")]
    of_source = _rows(
        tmp_path,
        "SELECT src_id, dst_id FROM kg_edges WHERE relation = 'of_source'")
    assert sorted(of_source) == [("chan:C1", "src:youtube"),
                                 ("chan:C2", "src:reddit")]


def test_entity_without_corpus_count_is_not_a_node(tmp_path):
    """Entities only in `entities` (no corpus-wide count) are excluded."""
    _run(tmp_path)
    lonely = _rows(
        tmp_path, "SELECT 1 FROM kg_nodes WHERE node_id = 'ent:Lonely'")
    assert lonely == []


def test_idempotent_rebuild(tmp_path):
    """Building twice yields identical node/edge counts and receipts."""
    cat = _build_catalog(tmp_path)
    fts = _build_fts(tmp_path)
    first = build_knowledge_graph(cat, fts)
    second = build_knowledge_graph(cat, fts)
    strip = lambda r: {k: v for k, v in r.items() if k != "seconds"}  # noqa: E731
    assert strip(first) == strip(second) == EXPECTED_RECEIPT
    # Tables were rebuilt, not appended: same totals as one fresh build.
    assert _rows(tmp_path, "SELECT COUNT(*) FROM kg_nodes") == [(10,)]
    assert _rows(tmp_path, "SELECT COUNT(*) FROM kg_edges") == [(7,)]


def test_dry_run_writes_nothing_and_plans_correctly(tmp_path):
    """--dry-run plans the same counts the real build produces, no writes."""
    plan = _run(tmp_path, dry_run=True)
    assert plan["dry_run"] is True
    strip = lambda r: {k: v for k, v in r.items()  # noqa: E731
                       if k not in ("seconds", "dry_run")}
    assert strip(plan) == EXPECTED_RECEIPT
    tables = _rows(
        tmp_path,
        "SELECT name FROM sqlite_master WHERE name LIKE 'kg_%' "
        "AND type = 'table'")
    assert tables == []
    # The real build afterward lands exactly on the planned counts.
    receipt = build_knowledge_graph(
        tmp_path / "catalog.sqlite", tmp_path / "fts5.sqlite")
    assert strip(receipt) == EXPECTED_RECEIPT
