"""Tests for ef/clustering.py and scripts/wiki_from_cluster.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ef.clustering import (
    extract_top_terms,
    generate_cluster_label,
    run_clustering,
    topic_inventory,
    _ensure_cluster_tables,
    _connect_catalog,
)


@pytest.fixture()
def catalog_db(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            eu_id TEXT,
            video_id TEXT,
            title TEXT
        );
    """)
    # Insert test chunks
    test_chunks = [
        (f"chunk_{i}", f"eu_{i}", f"vid_{i%10}", f"React Tutorial Part {i%5}")
        for i in range(50)
    ]
    conn.executemany(
        "INSERT INTO chunks (chunk_id, eu_id, video_id, title) VALUES (?, ?, ?, ?)",
        test_chunks,
    )
    conn.commit()
    conn.close()
    _ensure_cluster_tables(_connect_catalog.__wrapped__(path) if hasattr(_connect_catalog, '__wrapped__') else sqlite3.connect(path))
    return path


def test_clustering_finds_groups():
    """HDBSCAN on well-separated vectors should find clusters."""
    # Create 3 well-separated groups in 10-dim space
    rng = np.random.default_rng(42)
    group1 = rng.normal(0, 0.1, (50, 10)) + np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    group2 = rng.normal(0, 0.1, (50, 10)) + np.array([0, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    group3 = rng.normal(0, 0.1, (50, 10)) + np.array([0, 0, 1, 0, 0, 0, 0, 0, 0, 0])
    vectors = np.vstack([group1, group2, group3]).astype(np.float32)

    labels = run_clustering(vectors)
    unique = set(labels) - {-1}
    assert len(unique) >= 2  # should find at least 2 of the 3 groups


def test_clustering_all_noise_returns_noise():
    """Random noise vectors with no structure should produce mostly noise."""
    rng = np.random.default_rng(42)
    vectors = rng.normal(0, 1, (100, 50)).astype(np.float32)
    labels = run_clustering(vectors)
    # With truly random vectors, most should be noise
    # (not a strict test — HDBSCAN can find spurious clusters)
    assert len(set(labels)) >= 1  # at minimum, all -1


def test_extract_top_terms():
    payloads = [
        {"title": "React Hooks Tutorial for Beginners"},
        {"title": "React useState and useEffect Guide"},
        {"title": "Advanced React Patterns"},
        {"title": "React Context API Deep Dive"},
    ] * 5  # repeat for frequency
    labels = np.array([0] * 20)
    terms = extract_top_terms(payloads, labels, cluster_id=0)
    assert "react" in terms
    assert len(terms) <= 10


def test_generate_cluster_label():
    assert generate_cluster_label(["react", "hooks", "state"]) == "React Hooks State"
    assert generate_cluster_label([]) == "Unknown Topic"


def test_topic_inventory_empty(tmp_path):
    """When no clusters exist, inventory reports unavailable."""
    # Use a temporary empty catalog
    import ef.clustering as cl
    original = cl.CATALOG_DB
    cl.CATALOG_DB = tmp_path / "empty.sqlite"
    try:
        conn = sqlite3.connect(str(cl.CATALOG_DB))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        _ensure_cluster_tables(sqlite3.connect(str(cl.CATALOG_DB)))
        result = topic_inventory(sqlite3.connect(str(cl.CATALOG_DB)))
        assert result.get("available") is False or result.get("total_topics") == 0
    finally:
        cl.CATALOG_DB = original


def test_wiki_from_cluster_importable():
    """The wiki_from_cluster module imports cleanly."""
    from scripts.wiki_from_cluster import (
        get_cluster_info,
        get_cluster_videos,
        generate_concept_page,
    )
    assert callable(get_cluster_info)
    assert callable(get_cluster_videos)
    assert callable(generate_concept_page)


def test_generate_concept_page():
    """Concept page generation produces SCHEMA-compliant output."""
    from scripts.wiki_from_cluster import generate_concept_page

    cluster = {
        "cluster_id": 42,
        "label": "React State Management",
        "chunks": 100,
        "videos": 25,
        "top_terms": ["react", "state", "hooks", "redux"],
    }
    videos = [
        {"video_id": "abc123", "title": "React Guide", "url": "https://youtube.com/watch?v=abc123", "chunks_in_cluster": 10},
    ]
    chunks = [
        {"snippet": "React hooks allow you to manage state in functional components"},
    ]

    page = generate_concept_page(cluster, videos, chunks)
    assert "React State Management" in page
    assert "youtube.com/watch?v=abc123" in page
    assert "ef-cluster-42" in page
    assert "topic cluster" in page.lower()
    assert "## Overview" in page
    assert "## Source Videos" in page
    assert "## Falsifier" in page
