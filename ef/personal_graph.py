"""Typed personal-graph tables + validated-inference persistence.

The v2 schema from docs/design/personal-intelligence-system-2026-08-24.md.
Relational tables in catalog.sqlite — NO graph database (the design's
explicit decision: a graph data model is justified before a graph DB).

Tables are created idempotently on first use. The feedback endpoint is
live the moment the service routes it — the operator's directive:
deferred feedback = lost ground-truth history.

v2 contract fidelity (2026-08-24): store_validated_inference() is the
ONLY writer for inference-produced semantic objects. It consumes already
validated payloads, uses deterministic content-hash identity (reruns do
not duplicate semantics), and performs the whole run in one transaction —
a failure anywhere rolls back the entire semantic write.

Tests initialize the same schema against a temporary SQLite path via
connect(db_path=<tmp>) — never the production catalog.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS interests (
    interest_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,          -- domain|topic|subtopic|method|monitor
    parent_id TEXT REFERENCES interests(interest_id),
    temporal_state TEXT,         -- durable|active|current_problem|
                                 -- episodic|emerging|dormant
    stance TEXT,                 -- curiosity|learning|project|monitoring|
                                 -- entertainment
    confidence REAL,
    intensity REAL,
    persistence REAL,
    recency REAL,
    trajectory TEXT,             -- accelerating|steady|decelerating
    observed_vs_inferred TEXT,   -- observed|inferred|inferred_adjacent
    goal_id TEXT,
    evidence_json TEXT,          -- [{cluster_id, entity, doc, channel,
                                 --   month, source}]
    exclusions_json TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS goals (
    goal_id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    status TEXT DEFAULT 'open',  -- open|achieved|abandoned|reframed
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS questions (
    question_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    status TEXT DEFAULT 'open',  -- open|answered|dismissed|watching
    interest_id TEXT,
    opened_at TEXT
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    confidence TEXT,             -- low|moderate|high
    last_challenged_at TEXT
);
CREATE TABLE IF NOT EXISTS evidence_links (
    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_kind TEXT NOT NULL,      -- evidence|interest|claim|question
    src_id TEXT NOT NULL,
    dst_kind TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    relation TEXT NOT NULL,      -- supports|contradicts|about|answers|
                                 -- subtopic_of|related_to
    strength REAL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    ts TEXT NOT NULL,
    surface TEXT NOT NULL,       -- today|interests|regret|research
    item_kind TEXT NOT NULL,     -- interest|doc|cluster|claim|opportunity
    item_id TEXT NOT NULL,
    verdict TEXT NOT NULL,       -- useful|known_already|not_interested|
                                 -- wrong_inference|investigate|acted_on|
                                 -- save|more_like|less_like
    note TEXT
);
CREATE TABLE IF NOT EXISTS information_needs (
    need_id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    interest_id TEXT,
    goal_id TEXT,
    status TEXT,                 -- open|met
    created_at TEXT,
    updated_at TEXT,
    inference_run_id TEXT
);
CREATE TABLE IF NOT EXISTS regret_candidates (
    regret_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    why TEXT NOT NULL,
    confidence REAL,
    related_interest_ids_json TEXT,
    evidence_cluster_ids_json TEXT,
    status TEXT,                 -- open|dismissed|acted_on
    created_at TEXT,
    updated_at TEXT,
    inference_run_id TEXT
);
CREATE TABLE IF NOT EXISTS inference_runs (
    run_id TEXT PRIMARY KEY,
    provider TEXT,
    model TEXT,                  -- requested model (serving model NOT verified)
    prompt_version TEXT,
    candidate_policy TEXT,
    cluster_ids_json TEXT,
    result_hash TEXT,
    created_at TEXT,
    status TEXT                  -- running|success|failed
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_links_edge
    ON evidence_links(src_kind, src_id, dst_kind, dst_id, relation);
CREATE INDEX IF NOT EXISTS idx_interests_parent
    ON interests(parent_id);
CREATE INDEX IF NOT EXISTS idx_feedback_surface
    ON feedback(surface, item_kind);
"""

# Idempotent column additions for pre-existing tables (ALTER TABLE has no
# IF NOT EXISTS): (table, column, definition).
_COLUMN_ADDITIONS = (
    ("interests", "evidence_summary", "TEXT"),
    ("interests", "counterevidence", "TEXT"),
    ("interests", "inference_run_id", "TEXT"),
    ("goals", "updated_at", "TEXT"),
    ("goals", "inference_run_id", "TEXT"),
    ("questions", "updated_at", "TEXT"),
    ("questions", "inference_run_id", "TEXT"),
)


class InferencePersistenceError(RuntimeError):
    """The validated inference could not be persisted atomically."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create/extend all personal-graph tables idempotently."""
    conn.executescript(SCHEMA)
    existing: dict[str, set[str]] = {}
    for table, _col, _def in _COLUMN_ADDITIONS:
        if table not in existing:
            existing[table] = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for table, col, definition in _COLUMN_ADDITIONS:
        if col not in existing[table]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
    conn.commit()


def connect(db_path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or CATALOG), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Deterministic semantic identity
# ---------------------------------------------------------------------------

def _norm_text(value) -> str:
    return " ".join(str(value).strip().casefold().split())


def _digest(*parts) -> str:
    payload = "\x1f".join(
        _norm_text(p) for p in parts if p is not None and str(p).strip() != "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def interest_identity_id(kind: str, name: str) -> str:
    return f"int_{_digest(kind, name)}"


def goal_identity_id(statement: str) -> str:
    return f"goal_{_digest(statement)}"


def information_need_identity_id(statement: str, interest_id: str = None,
                                 goal_id: str = None) -> str:
    return f"need_{_digest(statement, interest_id, goal_id)}"


def question_identity_id(text: str, interest_id: str = None) -> str:
    return f"q_{_digest(text, interest_id)}"


def regret_identity_id(topic: str) -> str:
    return f"regret_{_digest(topic)}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _edge(conn: sqlite3.Connection, src_kind: str, src_id: str,
          dst_kind: str, dst_id: str, relation: str,
          run_id: str, now: str) -> None:
    """Insert a typed relationship edge; unique index makes reruns no-ops."""
    conn.execute(
        "INSERT OR IGNORE INTO evidence_links "
        "(src_kind, src_id, dst_kind, dst_id, relation, strength, created_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?)",
        (src_kind, src_id, dst_kind, dst_id, relation, now))


# ---------------------------------------------------------------------------
# Validated-inference persistence (single transaction)
# ---------------------------------------------------------------------------

def store_validated_inference(conn: sqlite3.Connection, payload: dict, *,
                              run_id: str, provider: str, model: str,
                              prompt_version: str, candidate_policy: str,
                              cluster_ids, result_hash: str) -> dict:
    """Persist one validated v2 inference as a complete typed graph.

    payload must already have passed the caller's mechanical contract
    validation (scripts/build_interest_graph.validate_inference). Any
    failure rolls back the entire semantic write — a partially persisted
    run can never masquerade as successful.

    Identities are deterministic content hashes, so replaying identical
    validated output upserts the same rows and leaves edge counts flat.
    """
    interests = payload["inferred_interests"]
    questions = payload["questions"]
    regrets = payload["regret_candidates"]
    now = _now()
    try:
        conn.execute(
            "INSERT INTO inference_runs (run_id, provider, model, "
            "prompt_version, candidate_policy, cluster_ids_json, "
            "result_hash, created_at, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, provider, model, prompt_version, candidate_policy,
             json.dumps([int(c) for c in cluster_ids]), result_hash, now,
             "running"))

        ids = {_norm_text(it["name"]):
               interest_identity_id(it["kind"], it["name"]) for it in interests}

        _upsert_goals(conn, interests, run_id, now)
        goal_ids = {_norm_text(it["goal"]): goal_identity_id(it["goal"])
                    for it in interests if it.get("goal")}
        _upsert_interests(conn, interests, ids, goal_ids, run_id, now)
        _store_parent_relations(conn, interests, ids, run_id, now)
        _store_information_needs(conn, interests, ids, goal_ids, run_id, now)
        _store_questions(conn, questions, ids, run_id, now)
        _store_regret_candidates(conn, regrets, ids, run_id, now)
        _store_evidence_and_related_edges(conn, interests, ids, run_id, now)

        conn.execute("UPDATE inference_runs SET status='success' "
                     "WHERE run_id=?", (run_id,))
        conn.commit()
        return {
            "run_id": run_id,
            "interests": len(interests),
            "goals": len(goal_ids),
            "information_needs": sum(
                1 for it in interests if it.get("information_need")),
            "questions": len(questions),
            "regret_candidates": len(regrets),
        }
    except Exception:
        conn.rollback()
        raise


def _upsert_goals(conn, interests, run_id: str, now: str) -> None:
    for it in interests:
        if not it.get("goal"):
            continue
        conn.execute(
            "INSERT INTO goals (goal_id, statement, status, created_at, "
            "updated_at, inference_run_id) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(goal_id) DO UPDATE SET "
            "updated_at=excluded.updated_at, "
            "inference_run_id=excluded.inference_run_id",
            (goal_identity_id(it["goal"]), it["goal"], "open", now, now,
             run_id))


def _upsert_interests(conn, interests, ids, goal_ids, run_id, now) -> None:
    for it in interests:
        iid = ids[_norm_text(it["name"])]
        gid = goal_ids.get(_norm_text(it["goal"])) if it.get("goal") else None
        conn.execute(
            "INSERT INTO interests (interest_id, name, kind, parent_id, "
            "temporal_state, stance, confidence, observed_vs_inferred, "
            "goal_id, evidence_json, exclusions_json, evidence_summary, "
            "counterevidence, updated_at, inference_run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(interest_id) DO UPDATE SET "
            "name=excluded.name, kind=excluded.kind, "
            "parent_id=excluded.parent_id, "
            "temporal_state=excluded.temporal_state, "
            "stance=excluded.stance, confidence=excluded.confidence, "
            "observed_vs_inferred=excluded.observed_vs_inferred, "
            "goal_id=excluded.goal_id, evidence_json=excluded.evidence_json, "
            "exclusions_json=excluded.exclusions_json, "
            "evidence_summary=excluded.evidence_summary, "
            "counterevidence=excluded.counterevidence, "
            "updated_at=excluded.updated_at, "
            "inference_run_id=excluded.inference_run_id",
            (iid, it["name"], it["kind"], None,
             it.get("temporal_state"), it.get("stance"),
             it.get("confidence"), it.get("observed_vs_inferred"),
             gid,
             json.dumps({"cluster_ids": it.get("cluster_ids", []),
                         "evidence_summary": it.get("evidence_summary", ""),
                         "related_to": it.get("related_to", [])},
                        ensure_ascii=False),
             json.dumps({"counterevidence": it.get("counterevidence")},
                        ensure_ascii=False),
             it.get("evidence_summary"), it.get("counterevidence"),
             now, run_id))


def _store_parent_relations(conn, interests, ids, run_id, now) -> None:
    for it in interests:
        if not it.get("parent"):
            continue
        child = ids[_norm_text(it["name"])]
        parent = ids[_norm_text(it["parent"])]
        conn.execute("UPDATE interests SET parent_id=? WHERE interest_id=?",
                     (parent, child))
        _edge(conn, "interest", child, "interest", parent, "subtopic_of",
              run_id, now)


def _store_information_needs(conn, interests, ids, goal_ids, run_id, now) -> None:
    for it in interests:
        if not it.get("information_need"):
            continue
        iid = ids[_norm_text(it["name"])]
        gid = goal_ids.get(_norm_text(it["goal"])) if it.get("goal") else None
        nid = information_need_identity_id(it["information_need"], iid, gid)
        conn.execute(
            "INSERT INTO information_needs (need_id, statement, interest_id, "
            "goal_id, status, created_at, updated_at, inference_run_id) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(need_id) DO UPDATE SET "
            "statement=excluded.statement, interest_id=excluded.interest_id, "
            "goal_id=excluded.goal_id, updated_at=excluded.updated_at, "
            "inference_run_id=excluded.inference_run_id",
            (nid, it["information_need"], iid, gid, "open", now, now, run_id))


def _store_questions(conn, questions, ids, run_id, now) -> None:
    for q in questions:
        iid = ids[_norm_text(q["interest"])]
        qid = question_identity_id(q["text"], iid)
        conn.execute(
            "INSERT INTO questions (question_id, text, status, interest_id, "
            "opened_at, updated_at, inference_run_id) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(question_id) DO UPDATE SET "
            "text=excluded.text, status=excluded.status, "
            "interest_id=excluded.interest_id, "
            "updated_at=excluded.updated_at, "
            "inference_run_id=excluded.inference_run_id",
            (qid, q["text"], q.get("status", "open"), iid, now, now, run_id))
        _edge(conn, "question", qid, "interest", iid, "about", run_id, now)


def _store_regret_candidates(conn, regrets, ids, run_id, now) -> None:
    for rc in regrets:
        related = [ids[_norm_text(n)] for n in rc.get("related_interests", [])]
        conn.execute(
            "INSERT INTO regret_candidates (regret_id, topic, why, "
            "confidence, related_interest_ids_json, "
            "evidence_cluster_ids_json, status, created_at, updated_at, "
            "inference_run_id) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(regret_id) DO UPDATE SET "
            "topic=excluded.topic, why=excluded.why, "
            "confidence=excluded.confidence, "
            "related_interest_ids_json=excluded.related_interest_ids_json, "
            "evidence_cluster_ids_json=excluded.evidence_cluster_ids_json, "
            "updated_at=excluded.updated_at, "
            "inference_run_id=excluded.inference_run_id",
            (regret_identity_id(rc["topic"]), rc["topic"], rc["why"],
             rc.get("confidence"), json.dumps(related),
             json.dumps([int(c) for c in rc.get("cluster_ids", [])]),
             "open", now, now, run_id))


def _store_evidence_and_related_edges(conn, interests, ids, run_id, now) -> None:
    for it in interests:
        iid = ids[_norm_text(it["name"])]
        for cid in it.get("cluster_ids", []):
            _edge(conn, "evidence_cluster", str(int(cid)), "interest", iid,
                  "supports", run_id, now)
        for related in it.get("related_to", []):
            _edge(conn, "interest", iid, "interest",
                  ids[_norm_text(related)], "related_to", run_id, now)


# ---------------------------------------------------------------------------
# Feedback capture (unchanged public surface)
# ---------------------------------------------------------------------------

def record_feedback(surface: str, item_kind: str, item_id: str,
                    verdict: str, note: str = "") -> bool:
    if verdict not in ("useful", "known_already", "not_interested",
                       "wrong_inference", "investigate", "acted_on",
                       "save", "more_like", "less_like"):
        return False
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO feedback (ts, surface, item_kind, item_id, "
            "verdict, note) VALUES (?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"), surface, item_kind,
             item_id, verdict, note))
        conn.commit()
        return True
    finally:
        conn.close()


def feedback_summary() -> dict:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT verdict, COUNT(*) FROM feedback "
            "GROUP BY 1 ORDER BY 2 DESC").fetchall()
        return {v: n for v, n in rows}
    finally:
        conn.close()
