"""Tests for ef/personal_graph.py — v2 contract-fidelity persistence.

All tests use temporary SQLite databases (connect(db_path=<tmp>)); the
production catalog is never touched. Synthetic topics only.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ef import personal_graph as pg


def valid_payload() -> dict:
    """A complete, contract-valid synthetic inference payload.

    Fictional topics only (distributed databases, compiler optimization,
    gardening, astronomy) — no real user-interest data.
    """
    return {
        "inferred_interests": [
            {
                "name": "Distributed Databases",
                "kind": "domain",
                "parent": None,
                "temporal_state": "durable",
                "stance": "learning",
                "confidence": 0.9,
                "observed_vs_inferred": "observed",
                "goal": "Build a replicated multi-region datastore",
                "information_need": "Which consensus algorithm survives "
                                    "partitioned minorities",
                "cluster_ids": [1, 2],
                "evidence_summary": "Clusters 1-2: 30 docs across 8 channels",
                "counterevidence": None,
                "related_to": ["Compiler Optimization"],
            },
            {
                "name": "Raft Consensus",
                "kind": "subtopic",
                "parent": "Distributed Databases",
                "temporal_state": "active",
                "stance": "project",
                "confidence": 0.8,
                "observed_vs_inferred": "observed",
                "goal": None,
                "information_need": None,
                "cluster_ids": [2],
                "evidence_summary": "Cluster 2: leader-election deep dives",
                "counterevidence": "single recreational lecture",
                "related_to": [],
            },
            {
                "name": "Compiler Optimization",
                "kind": "topic",
                "parent": None,
                "temporal_state": "emerging",
                "stance": "curiosity",
                "confidence": 0.6,
                "observed_vs_inferred": "inferred_adjacent",
                "goal": None,
                "information_need": "Cost model of profile-guided optimization",
                "cluster_ids": [3],
                "evidence_summary": "Cluster 3: PGO discussions",
                "counterevidence": None,
                "related_to": ["Distributed Databases"],
            },
        ],
        "questions": [
            {
                "text": "Does Raft make progress during a minority partition?",
                "interest": "Raft Consensus",
                "status": "open",
            },
            {
                "text": "How much does PGO buy on ARM?",
                "interest": "Compiler Optimization",
                "status": "watching",
            },
        ],
        "regret_candidates": [
            {
                "topic": "Astronomy",
                "why": "Adjacent to observed physics consumption but absent "
                       "from the corpus",
                "label": "inferred_adjacent",
                "confidence": 0.5,
                "cluster_ids": [4],
                "related_interests": ["Distributed Databases"],
            },
        ],
    }


SUPPLIED_CLUSTER_IDS = [1, 2, 3, 4]


def run_meta(**overrides) -> dict:
    meta = {
        "run_id": "run_20260824T000000_test",
        "provider": "codex",
        "model": "gpt-5.6-luna",
        "prompt_version": "v2.1-contract-fidelity",
        "candidate_policy": "top25-breadth-biased",
        "cluster_ids": SUPPLIED_CLUSTER_IDS,
        "result_hash": "a" * 64,
    }
    meta.update(overrides)
    return meta


@pytest.fixture
def db(tmp_path):
    conn = pg.connect(str(tmp_path / "personal_graph.sqlite"))
    yield conn
    conn.close()


def store(conn, payload=None, **meta_overrides):
    return pg.store_validated_inference(
        conn, payload or valid_payload(), **run_meta(**meta_overrides))


def count(conn, table) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_schema_creates_all_tables(tmp_path):
    conn = pg.connect(str(tmp_path / "pg.sqlite"))
    try:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("interests", "goals", "questions", "claims",
                      "evidence_links", "feedback", "information_needs",
                      "regret_candidates", "inference_runs"):
            assert table in tables, f"missing table {table}"
        indexes = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_evidence_links_edge" in indexes
    finally:
        conn.close()


def test_ensure_schema_is_idempotent_and_extends_columns(db):
    cols = {row[1] for row in db.execute("PRAGMA table_info(interests)")}
    assert {"evidence_summary", "counterevidence", "inference_run_id"} <= cols
    pg.ensure_schema(db)  # second application must not raise
    pg.ensure_schema(db)


# ---------------------------------------------------------------------------
# Typed persistence
# ---------------------------------------------------------------------------

def test_store_persists_full_typed_graph(db):
    summary = store(db)
    assert summary["interests"] == 3
    assert summary["goals"] == 1
    assert summary["information_needs"] == 2
    assert summary["questions"] == 2
    assert summary["regret_candidates"] == 1

    interests = {r["name"]: r for r in
                 db.execute("SELECT * FROM interests")}
    assert set(interests) == {"Distributed Databases", "Raft Consensus",
                              "Compiler Optimization"}

    # Goal row exists and interest.goal_id resolves to it.
    goal = db.execute("SELECT * FROM goals").fetchone()
    assert goal["statement"] == "Build a replicated multi-region datastore"
    dd = interests["Distributed Databases"]
    assert dd["goal_id"] == goal["goal_id"]

    # Information need exists and resolves.
    need = db.execute(
        "SELECT * FROM information_needs WHERE statement LIKE 'Which "
        "consensus%'").fetchone()
    assert need is not None
    assert need["interest_id"] == dd["interest_id"]

    # Parent relationship resolves.
    raft = interests["Raft Consensus"]
    assert raft["parent_id"] == dd["interest_id"]

    # Questions resolve to real interest IDs.
    for row in db.execute("SELECT * FROM questions"):
        assert row["interest_id"] in {i["interest_id"]
                                      for i in interests.values()}

    # Regret candidate persisted with typed ids.
    regret = db.execute("SELECT * FROM regret_candidates").fetchone()
    assert regret["topic"] == "Astronomy"
    assert dd["interest_id"] in regret["related_interest_ids_json"]
    assert "4" in regret["evidence_cluster_ids_json"]

    # Evidence provenance links: one supports row per interest cluster ref.
    supports = db.execute(
        "SELECT * FROM evidence_links WHERE relation='supports' "
        "AND dst_kind='interest'").fetchall()
    assert len(supports) == 4  # [1,2] + [2] + [3]

    # Parent/subtopic edge exists.
    assert db.execute(
        "SELECT COUNT(*) FROM evidence_links WHERE relation='subtopic_of' "
        "AND src_id=? AND dst_id=?",
        (raft["interest_id"], dd["interest_id"])).fetchone()[0] == 1

    # Related-interest edge exists (both directions declared in payload).
    assert db.execute(
        "SELECT COUNT(*) FROM evidence_links WHERE relation='related_to' "
        "AND src_id=? AND dst_id=?",
        (dd["interest_id"],
         interests["Compiler Optimization"]["interest_id"])
    ).fetchone()[0] == 1


def test_store_no_unresolved_relationship_ids(db):
    store(db)
    interest_ids = {r[0] for r in db.execute(
        "SELECT interest_id FROM interests")}
    goal_ids = {r[0] for r in db.execute("SELECT goal_id FROM goals")}

    for row in db.execute("SELECT parent_id, goal_id FROM interests"):
        assert row["parent_id"] is None or row["parent_id"] in interest_ids
        assert row["goal_id"] is None or row["goal_id"] in goal_ids
    for row in db.execute("SELECT interest_id, goal_id "
                          "FROM information_needs"):
        assert row["interest_id"] in interest_ids
        assert row["goal_id"] is None or row["goal_id"] in goal_ids
    for row in db.execute("SELECT interest_id FROM questions"):
        assert row[0] in interest_ids
    for row in db.execute("SELECT related_interest_ids_json "
                          "FROM regret_candidates"):
        import json
        for rid in json.loads(row[0]):
            assert rid in interest_ids
    for row in db.execute("SELECT src_kind, src_id, dst_kind, dst_id "
                          "FROM evidence_links"):
        if row["dst_kind"] == "interest":
            assert row["dst_id"] in interest_ids
        if row["src_kind"] in ("interest", "question"):
            assert row["src_id"] in interest_ids | {
                r[0] for r in db.execute("SELECT question_id FROM questions")}


def test_inference_run_provenance(db):
    store(db)
    run = db.execute("SELECT * FROM inference_runs").fetchone()
    assert run["status"] == "success"
    assert run["provider"] == "codex"
    assert run["model"] == "gpt-5.6-luna"  # recorded as requested
    assert run["prompt_version"] == "v2.1-contract-fidelity"
    assert run["candidate_policy"] == "top25-breadth-biased"
    assert run["cluster_ids_json"] == "[1, 2, 3, 4]"
    assert run["result_hash"] == "a" * 64
    # Every semantic row carries its run.
    for table in ("interests", "goals", "information_needs", "questions",
                  "regret_candidates"):
        for row in db.execute(f"SELECT inference_run_id FROM {table}"):
            assert row[0] == run["run_id"]


def test_result_hash_stability_is_caller_supplied(db):
    store(db, result_hash="f" * 64)
    run = db.execute("SELECT result_hash FROM inference_runs").fetchone()
    assert run[0] == "f" * 64


# ---------------------------------------------------------------------------
# Determinism / idempotence
# ---------------------------------------------------------------------------

def test_deterministic_ids_across_databases(tmp_path):
    ids_by_db = []
    for i in range(2):
        conn = pg.connect(str(tmp_path / f"pg{i}.sqlite"))
        try:
            store(conn)
            ids_by_db.append({
                r[0] for r in conn.execute(
                    "SELECT interest_id FROM interests")})
        finally:
            conn.close()
    assert ids_by_db[0] == ids_by_db[1]
    assert all(i.startswith("int_") for i in ids_by_db[0])
    conn = pg.connect(str(tmp_path / "pg_prefix.sqlite"))
    try:
        store(conn)
        assert conn.execute(
            "SELECT goal_id FROM goals").fetchone()[0].startswith("goal_")
        assert conn.execute(
            "SELECT need_id FROM information_needs"
        ).fetchone()[0].startswith("need_")
        assert conn.execute(
            "SELECT question_id FROM questions"
        ).fetchone()[0].startswith("q_")
        assert conn.execute(
            "SELECT regret_id FROM regret_candidates"
        ).fetchone()[0].startswith("regret_")
    finally:
        conn.close()


def test_identical_rerun_does_not_duplicate(db):
    store(db)
    snapshot = {t: count(db, t) for t in (
        "interests", "goals", "information_needs", "questions",
        "regret_candidates", "evidence_links")}
    store(db, run_id="run_20260824T000001_test")
    after = {t: count(db, t) for t in snapshot}
    assert after == snapshot  # semantic objects and edges stay flat
    assert count(db, "inference_runs") == 2  # runs are events


# ---------------------------------------------------------------------------
# Transactionality / fail-closed
# ---------------------------------------------------------------------------

def test_failure_mid_persistence_rolls_back_everything(db, monkeypatch):
    def boom(conn, questions, ids, run_id, now):
        raise RuntimeError("injected failure after interests were written")

    monkeypatch.setattr(pg, "_store_questions", boom)
    with pytest.raises(RuntimeError, match="injected failure"):
        store(db)
    for table in ("interests", "goals", "information_needs", "questions",
                  "regret_candidates", "evidence_links", "inference_runs"):
        assert count(db, table) == 0, f"{table} not rolled back"


def test_rollback_leaves_connection_usable(db, monkeypatch):
    monkeypatch.setattr(pg, "_store_regret_candidates",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("late failure")))
    with pytest.raises(RuntimeError):
        store(db)
    assert count(db, "interests") == 0
    monkeypatch.undo()
    store(db)  # connection is clean and the same store can succeed
    assert count(db, "interests") == 3
