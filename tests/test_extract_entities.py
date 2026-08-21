"""Tests for the entity extraction script.

The script does three things in sequence:
  1. `cluster_chunks` — fetch representative excerpts for a topic cluster
     via the PK-reopen path (chunk -> eu -> authority_ref -> transcript span).
  2. `extract_cluster` — send the excerpts to an LLM, parse the JSON entity
     list, validate, and store in the `entities` table.
  3. `refresh_counts` — count each entity's corpus-wide footprint via FTS5.

The LLM call is the only real-world side effect we can't allow in tests.
`ask_llm` is patched to return canned strings, so the rest of the pipeline
runs against a fresh in-memory catalog + TDB + FTS5 database.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# --- schema fixtures ---------------------------------------------------------

CATALOG_SCHEMA = textwrap.dedent("""
    CREATE TABLE eu (
        eu_id TEXT PRIMARY KEY,
        video_id TEXT NOT NULL,
        authority_ref TEXT NOT NULL
    );
    CREATE TABLE chunk (
        chunk_id TEXT PRIMARY KEY,
        eu_id TEXT NOT NULL,
        start_char INTEGER NOT NULL,
        end_char INTEGER NOT NULL
    );
    CREATE TABLE topic_clusters (
        cluster_id INTEGER PRIMARY KEY,
        label TEXT,
        member_count INTEGER DEFAULT 0
    );
    CREATE TABLE chunk_clusters (
        chunk_id TEXT PRIMARY KEY,
        video_id TEXT,
        cluster_id INTEGER
    );
""").strip()

TDB_SCHEMA = textwrap.dedent("""
    CREATE TABLE transcript_cache (
        cache_key TEXT PRIMARY KEY,
        video_id TEXT NOT NULL,
        transcript TEXT NOT NULL
    );
""").strip()


def _build_catalog(tmp_path, *, cluster_rows=(), eu_rows=(), chunk_rows=(),
                   cluster_members=()):
    cat = tmp_path / "catalog.sqlite"
    conn = sqlite3.connect(cat)
    conn.executescript(CATALOG_SCHEMA)
    conn.executemany(
        "INSERT INTO topic_clusters (cluster_id, label, member_count) "
        "VALUES (?, ?, ?)", cluster_rows)
    conn.executemany(
        "INSERT INTO eu (eu_id, video_id, authority_ref) VALUES (?, ?, ?)",
        eu_rows)
    conn.executemany(
        "INSERT INTO chunk (chunk_id, eu_id, start_char, end_char) "
        "VALUES (?, ?, ?, ?)", chunk_rows)
    conn.executemany(
        "INSERT INTO chunk_clusters (chunk_id, video_id, cluster_id) "
        "VALUES (?, ?, ?)", cluster_members)
    conn.commit()
    conn.close()
    return cat


def _build_tdb(tmp_path, *, rows=()):
    tdb = tmp_path / "transcripts.sqlite"
    conn = sqlite3.connect(tdb)
    conn.executescript(TDB_SCHEMA)
    conn.executemany(
        "INSERT INTO transcript_cache (cache_key, video_id, transcript) "
        "VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return tdb


def _build_fts(tmp_path, *, rows=()):
    """Build the fts5.sqlite mirror that the production schema uses."""
    fts = tmp_path / "fts5.sqlite"
    conn = sqlite3.connect(fts)
    conn.execute(
        "CREATE VIRTUAL TABLE chunks USING fts5(text, chunk_id UNINDEXED)")
    conn.executemany(
        "INSERT INTO chunks(text, chunk_id) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return fts


# --- module-level fixture: point the module at tmp DBs + mock LLM ----------


@pytest.fixture
def ee(tmp_path, monkeypatch):
    """Reload extract_entities pointed at tmp DBs and patch ask_llm."""
    import scripts.extract_entities as mod
    mod = importlib.reload(mod)
    monkeypatch.setattr(mod, "CATALOG", tmp_path / "catalog.sqlite")
    monkeypatch.setattr(mod, "TDB", tmp_path / "transcripts.sqlite")
    monkeypatch.setattr(mod, "FTS", tmp_path / "fts5.sqlite")
    return mod


@pytest.fixture
def fake_llm(monkeypatch):
    """Patch ask_llm via ef.qa._provider_chain with a deterministic responder.

    `providers` is a list of (name, response, raise) tuples. When empty,
    a single provider is created that returns `state["response"]` and
    raises `state["raise"]` (if set). Use `state["providers"]` for custom
    multi-provider chains.
    """
    import ef.qa
    state = {"calls": [], "response": None, "raise": None, "providers": []}

    def make_provider(responder):
        def fn(question, ctx):
            state["calls"].append(question)
            if responder.get("raise") is not None:
                raise responder["raise"]
            return responder.get("response")
        return fn

    def fake_chain():
        if state["providers"]:
            return [(name, make_provider(p))
                    for name, p in state["providers"]]
        return [("fake", make_provider(state))]

    monkeypatch.setattr(ef.qa, "_provider_chain", fake_chain)
    return state


# === cluster_chunks =========================================================


def test_cluster_chunks_empty_when_no_members(ee, tmp_path):
    cat = _build_catalog(tmp_path, cluster_rows=[(1, "ai", 0)])
    conn = sqlite3.connect(cat)
    assert ee.cluster_chunks(conn, 1) == ""
    conn.close()


def test_cluster_chunks_concatenates_via_pk_reopen(ee, tmp_path):
    """chunk -> eu -> authority_ref -> transcript span path."""
    text = "The quick brown fox jumps over the lazy dog. " * 8  # ~360 chars
    _build_catalog(
        tmp_path,
        cluster_rows=[(1, "ai", 1)],
        eu_rows=[("eu1", "v1", "v1")],
        chunk_rows=[("c1", "eu1", 0, 100)],
        cluster_members=[("c1", "v1", 1)],
    )
    _build_tdb(tmp_path, rows=[("v1", "v1", text)])

    cat = sqlite3.connect(tmp_path / "catalog.sqlite")
    out = ee.cluster_chunks(cat, 1, limit=6)
    cat.close()
    assert "quick brown fox" in out


def test_cluster_chunks_limits_per_cluster_and_truncates(ee, tmp_path):
    """Limits to CHUNKS_PER_CLUSTER=6 chunks and truncates to 12k chars."""
    # 10 chunks in the cluster, only first 6 should appear.
    rows_chunk = [(f"c{i}", "eu1", 0, 50) for i in range(10)]
    rows_member = [(f"c{i}", "v1", 1) for i in range(10)]
    rows_tdb = [("v1", "v1", "lorem ipsum dolor sit amet " * 200)]

    _build_catalog(
        tmp_path,
        cluster_rows=[(1, "ai", 10)],
        eu_rows=[("eu1", "v1", "v1")],
        chunk_rows=rows_chunk,
        cluster_members=rows_member,
    )
    _build_tdb(tmp_path, rows=rows_tdb)

    cat = sqlite3.connect(tmp_path / "catalog.sqlite")
    out = ee.cluster_chunks(cat, 1, limit=6)
    cat.close()
    # 6 chunks, each ~3.2k chars => ~19.2k chars; truncated to 12000.
    assert len(out) <= 12000
    # The '---' separator appears between chunks (5 separators for 6 chunks).
    assert out.count("---") == 5


def test_cluster_chunks_skips_chunks_without_span_row(ee, tmp_path):
    """A chunk_clusters row whose chunk has no matching chunk/eu row is skipped."""
    _build_catalog(
        tmp_path,
        cluster_rows=[(1, "ai", 1)],
        # c1 has no matching chunk row -> skipped
        cluster_members=[("c1", "v1", 1)],
    )
    _build_tdb(tmp_path)
    cat = sqlite3.connect(tmp_path / "catalog.sqlite")
    out = ee.cluster_chunks(cat, 1)
    cat.close()
    assert out == ""


# === ask_llm =================================================================


def test_ask_llm_returns_first_nonempty(ee, fake_llm):
    fake_llm["response"] = "[]"
    assert ee.ask_llm("anything") == "[]"


def test_ask_llm_skips_raising_provider(ee, fake_llm):
    """A raising provider is skipped; the next provider's response wins."""
    fake_llm["providers"] = [
        ("a", {"raise": RuntimeError("provider down")}),
        ("b", {"response": "[]"}),
    ]
    assert ee.ask_llm("q") == "[]"
    # Both providers were tried.
    assert len(fake_llm["calls"]) == 2


def test_ask_llm_returns_none_when_all_fail(ee, fake_llm):
    fake_llm["providers"] = [
        ("a", {"raise": RuntimeError("nope")}),
        ("b", {"raise": RuntimeError("still nope")}),
    ]
    assert ee.ask_llm("q") is None


def test_ask_llm_skips_empty_response(ee, fake_llm):
    """A provider returning empty/falsy is skipped."""
    fake_llm["providers"] = [
        ("a", {"response": ""}),
        ("b", {"response": "[]"}),
    ]
    assert ee.ask_llm("q") == "[]"


# === extract_cluster ========================================================


def _seed_one_cluster(ee, tmp_path, *, text, cluster_id=1, label="ai",
                      video_id="v1"):
    """Seed catalog + tdb with one cluster whose chunks all reuse the same
    transcript text. Returns the catalog connection."""
    chunk_rows = [(f"c{i}", "eu1", 0, min(100, len(text))) for i in range(3)]
    member_rows = [(f"c{i}", video_id, cluster_id) for i in range(3)]
    _build_catalog(
        tmp_path,
        cluster_rows=[(cluster_id, label, 3)],
        eu_rows=[("eu1", video_id, video_id)],
        chunk_rows=chunk_rows,
        cluster_members=member_rows,
    )
    _build_tdb(tmp_path, rows=[(video_id, video_id, text)])
    return sqlite3.connect(tmp_path / "catalog.sqlite")


def test_extract_cluster_skips_short_text(ee, fake_llm, tmp_path):
    """If cluster text is < 200 chars, ask_llm is never called."""
    cat = _seed_one_cluster(ee, tmp_path, text="tiny" * 10)  # 40 chars
    n = ee.extract_cluster(cat, 1, "ai")
    cat.close()
    assert n == 0
    assert fake_llm["calls"] == []  # LLM never invoked


def test_extract_cluster_parses_json_array(ee, fake_llm, tmp_path):
    payload = json.dumps([
        {"name": "PyTorch", "type": "TECH", "mentions": 5},
        {"name": "OpenAI", "label": "ORG", "mentions": 3},
    ])
    fake_llm["response"] = "Some prose intro\n" + payload + "\nThat's it."
    cat = _seed_one_cluster(ee, tmp_path,
                            text="lorem ipsum " * 50)  # 600 chars
    ee._ensure_tables(cat)
    n = ee.extract_cluster(cat, 1, "ai")
    assert n == 2
    rows = cat.execute("SELECT entity, label, mentions FROM entities").fetchall()
    cat.close()
    assert ("PyTorch", "TECH", 5) in rows
    assert ("OpenAI", "ORG", 3) in rows


def test_extract_cluster_returns_zero_on_malformed_json(ee, fake_llm, tmp_path):
    """A non-JSON response yields 0 (re.search finds no [...] array)."""
    fake_llm["response"] = "I cannot extract entities from this text."
    cat = _seed_one_cluster(ee, tmp_path, text="lorem ipsum " * 50)
    ee._ensure_tables(cat)
    n = ee.extract_cluster(cat, 1, "ai")
    cat.close()
    assert n == 0


def test_extract_cluster_returns_zero_on_invalid_json(ee, fake_llm, tmp_path):
    """A [...] block that doesn't parse as JSON is silently dropped."""
    fake_llm["response"] = "[not valid json"
    cat = _seed_one_cluster(ee, tmp_path, text="lorem ipsum " * 50)
    ee._ensure_tables(cat)
    n = ee.extract_cluster(cat, 1, "ai")
    cat.close()
    assert n == 0


def test_extract_cluster_validates_label_set(ee, fake_llm, tmp_path):
    """Entities with unknown labels are dropped; valid ones are kept."""
    payload = json.dumps([
        {"name": "Tom", "type": "PERSON"},
        {"name": "Mystery", "type": "ALCHEMY"},  # not in allowed set
    ])
    fake_llm["response"] = payload
    cat = _seed_one_cluster(ee, tmp_path, text="lorem ipsum " * 50)
    ee._ensure_tables(cat)
    n = ee.extract_cluster(cat, 1, "ai")
    assert n == 1
    rows = cat.execute("SELECT entity FROM entities").fetchall()
    cat.close()
    assert rows == [("Tom",)]


def test_extract_cluster_dedupes_by_pk(ee, fake_llm, tmp_path):
    """INSERT OR REPLACE means duplicate names in one response collapse."""
    payload = json.dumps([
        {"name": "PyTorch", "type": "TECH", "mentions": 5},
        {"name": "PyTorch", "type": "TECH", "mentions": 7},
    ])
    fake_llm["response"] = payload
    cat = _seed_one_cluster(ee, tmp_path, text="lorem ipsum " * 50)
    ee._ensure_tables(cat)
    n = ee.extract_cluster(cat, 1, "ai")
    # n counts inserts, so both insert attempts are counted (the second
    # replaces the first in the table but the loop still incremented n).
    assert n == 2
    rows = cat.execute("SELECT mentions FROM entities WHERE entity='PyTorch'"
                       ).fetchall()
    cat.close()
    # Last write wins: mentions is 7, not 5.
    assert rows == [(7,)]


def test_extract_cluster_returns_zero_when_llm_returns_none(
        ee, fake_llm, tmp_path):
    fake_llm["providers"] = [("a", {"raise": RuntimeError("nope")})]
    cat = _seed_one_cluster(ee, tmp_path, text="lorem ipsum " * 50)
    ee._ensure_tables(cat)
    n = ee.extract_cluster(cat, 1, "ai")
    cat.close()
    assert n == 0


# === refresh_counts =========================================================


def test_refresh_counts_uses_fts_for_corpus_wide_footprint(ee, tmp_path):
    """Each entity's chunk_count reflects FTS matches in fts5.sqlite."""
    # Seed: PyTorch appears in 2 chunks; TensorFlow in 1; Bedrock in 0.
    _build_fts(tmp_path, rows=[
        ("PyTorch is great for deep learning", "ck1"),
        ("We use PyTorch and JAX", "ck2"),
        ("TensorFlow is older", "ck3"),
    ])
    cat = _build_catalog(
        tmp_path,
        cluster_rows=[(1, "ai", 0)],
    )
    conn = sqlite3.connect(cat)
    ee._ensure_tables(conn)
    conn.executemany(
        "INSERT INTO entities (entity, label, cluster_id, mentions, "
        "extracted_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("PyTorch", "TECH", 1, 3, "2026-08-21T00:00:00Z"),
            ("TensorFlow", "TECH", 1, 2, "2026-08-21T00:00:00Z"),
            ("Bedrock", "PRODUCT", 1, 5, "2026-08-21T00:00:00Z"),
        ],
    )
    conn.commit()
    n = ee.refresh_counts(conn)
    conn.close()
    assert n == 3
    rows = dict(
        (e, c) for e, c in
        sqlite3.connect(tmp_path / "catalog.sqlite").execute(
            "SELECT entity, chunk_count FROM entity_corpus"
        ).fetchall()
    )
    assert rows["PyTorch"] == 2
    assert rows["TensorFlow"] == 1
    assert rows["Bedrock"] == 0


def test_refresh_counts_filters_low_mention_entities(ee, tmp_path):
    """Entities with SUM(mentions) < 2 are skipped (HAVING clause)."""
    _build_fts(tmp_path, rows=[("PyTorch mentioned once", "ck1")])
    cat = _build_catalog(tmp_path, cluster_rows=[(1, "ai", 0)])
    conn = sqlite3.connect(cat)
    ee._ensure_tables(conn)
    conn.executemany(
        "INSERT INTO entities (entity, label, cluster_id, mentions, "
        "extracted_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("PyTorch", "TECH", 1, 1, "2026-08-21T00:00:00Z"),
            ("Nobody", "CONCEPT", 1, 5, "2026-08-21T00:00:00Z"),
        ],
    )
    conn.commit()
    n = ee.refresh_counts(conn)
    conn.close()
    rows = {e for (e,) in sqlite3.connect(
        tmp_path / "catalog.sqlite").execute(
        "SELECT entity FROM entity_corpus").fetchall()}
    # PyTorch was excluded (mentions=1); Nobody was kept (mentions=5,
    # 0 FTS matches but the row is still recorded).
    assert rows == {"Nobody"}


def test_refresh_counts_dedupes_per_entity_label(ee, tmp_path):
    """If the same entity has two labels, only the first GROUP BY row is kept."""
    _build_fts(tmp_path, rows=[("PyTorch in chunk", "ck1")])
    cat = _build_catalog(tmp_path, cluster_rows=[(1, "ai", 0)])
    conn = sqlite3.connect(cat)
    ee._ensure_tables(conn)
    conn.executemany(
        "INSERT INTO entities (entity, label, cluster_id, mentions, "
        "extracted_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("PyTorch", "TECH", 1, 3, "2026-08-21T00:00:00Z"),
            ("PyTorch", "ORG", 2, 4, "2026-08-21T00:00:00Z"),
        ],
    )
    conn.commit()
    n = ee.refresh_counts(conn)
    conn.close()
    assert n == 1
    row = sqlite3.connect(tmp_path / "catalog.sqlite").execute(
        "SELECT label, chunk_count FROM entity_corpus WHERE entity='PyTorch'"
    ).fetchone()
    # One of TECH/ORG survives — depends on GROUP BY ordering. We assert only
    # that exactly one was kept.
    assert row[0] in ("TECH", "ORG")
    assert row[1] == 1


def test_refresh_counts_handles_fts_error_gracefully(ee, tmp_path):
    """An entity with an FTS-unparseable name does not crash the refresh.

    The f'"{entity}"' wrapping is naive; a name with a stray " breaks FTS5
    syntax. The `except Exception: continue` handler swallows the error and
    the entity is silently skipped (no row in entity_corpus).
    """
    _build_fts(tmp_path, rows=[("regular chunk", "ck1")])
    cat = _build_catalog(tmp_path, cluster_rows=[(1, "ai", 0)])
    conn = sqlite3.connect(cat)
    ee._ensure_tables(conn)
    conn.execute(
        "INSERT INTO entities (entity, label, cluster_id, mentions, "
        "extracted_at) VALUES (?, ?, ?, ?, ?)",
        ('weird"name', "CONCEPT", 1, 5, "2026-08-21T00:00:00Z"),
    )
    conn.commit()
    n = ee.refresh_counts(conn)  # must not raise
    conn.close()
    # Skipped entirely — the entity never made it into entity_corpus.
    assert n == 0
    rows = sqlite3.connect(tmp_path / "catalog.sqlite").execute(
        "SELECT entity FROM entity_corpus").fetchall()
    assert rows == []


def test_refresh_counts_fts_operator_chars_returns_zero_count(ee, tmp_path):
    """An entity name containing FTS5 operator tokens (OR, AND, NOT, NEAR)
    is wrapped in a phrase query, returning 0 matches. The entity is still
    recorded in entity_corpus with chunk_count=0 (not silently skipped).
    This is the FTS5-operator case, distinct from the FTS5-syntax-error
    case covered by test_refresh_counts_handles_fts_error_gracefully.
    """
    _build_fts(tmp_path, rows=[
        ("Node.js is great for server-side JavaScript", "ck1"),
        ("TensorFlow competes with PyTorch", "ck2"),
    ])
    cat = _build_catalog(tmp_path, cluster_rows=[(1, "ai", 0)])
    conn = sqlite3.connect(cat)
    ee._ensure_tables(conn)
    # Entity name with FTS5 operator tokens. FTS5 phrase search for
    # "Node.js OR thing" matches only chunks containing that exact phrase
    # (literal OR is part of the phrase, not a boolean operator).
    conn.execute(
        "INSERT INTO entities (entity, label, cluster_id, mentions, "
        "extracted_at) VALUES (?, ?, ?, ?, ?)",
        ("Node.js OR thing", "TECH", 1, 5, "2026-08-21T00:00:00Z"),
    )
    conn.commit()
    n = ee.refresh_counts(conn)
    conn.close()
    # Entity IS recorded, with chunk_count=0 (not skipped).
    assert n == 1
    row = sqlite3.connect(tmp_path / "catalog.sqlite").execute(
        "SELECT chunk_count FROM entity_corpus WHERE entity=?",
        ("Node.js OR thing",),
    ).fetchone()
    assert row == (0,)
