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
import os
import sqlite3
import time
import uuid
from pathlib import Path

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")

# Test override for the feedback/impression contract writers (same pattern
# as YTIS_FORMAL_LEDGER_PATH). Production behavior is unchanged when unset.
_FEEDBACK_DB_ENV = "YTIS_FEEDBACK_DB"


def _feedback_db(db_path):
    return db_path or os.environ.get(_FEEDBACK_DB_ENV)

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
-- -----------------------------------------------------------------------
-- Impression + feedback-event contract (2026-08-26).
-- Immutable append-only event log separating FEEDBACK/OUTCOME SIGNAL
-- (feedback_events) from WORKFLOW STATE (item_workflow_state). The legacy
-- feedback table is preserved read-only history; nothing writes it anymore.
-- NOTE: item_id namespaces differ — legacy rows store raw cluster ids and
-- title[:40] text; contract rows store "cluster:<id>" / "video:<id>". The
-- two tables are not joinable on item_id by design.
-- Fields the current system cannot populate are left NULL — explicit
-- unknown, never fabricated (propensity/experiment stay NULL until a
-- randomized policy actually exists).
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidate_sets (
    candidate_set_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    surface TEXT NOT NULL,
    ranking_policy TEXT NOT NULL,
    ranking_policy_version TEXT NOT NULL,
    items_json TEXT NOT NULL          -- ordered [{item_kind,item_id,
                                      --   rank_position, why_surfaced,...}]
);
CREATE TABLE IF NOT EXISTS impressions (
    impression_id TEXT PRIMARY KEY,
    candidate_set_id TEXT NOT NULL,
    surfaced_at TEXT NOT NULL,
    surface TEXT NOT NULL,            -- today|interests|regret|research
    ranking_policy TEXT NOT NULL,
    ranking_policy_version TEXT NOT NULL,
    item_kind TEXT NOT NULL,          -- interest|doc|cluster|claim|opportunity
    item_id TEXT NOT NULL,
    item_label TEXT,
    rank_position INTEGER,
    score REAL,                       -- NULL: current policies do not score
    why_surfaced TEXT,                -- emerging|recent_doc|dormant|...
    origin_interest_id TEXT,          -- NULL: no interest attribution yet
    actor_context TEXT,               -- NULL: single-operator system
    world_signal_json TEXT,           -- NULL when unavailable
    personal_relevance_json TEXT,     -- NULL when unavailable
    provenance TEXT,                  -- renderer identifier
    experiment_id TEXT,               -- NULL: no experiments running
    propensity REAL                   -- NULL: no randomized policy exists
);
CREATE TABLE IF NOT EXISTS feedback_events (
    feedback_event_id TEXT PRIMARY KEY,
    impression_id TEXT,               -- NULL = unattributed/legacy-style
    surface TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    note TEXT,
    occurred_at TEXT NOT NULL,
    source_route TEXT NOT NULL,       -- e.g. "POST /feedback"
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_events_idem
    ON feedback_events(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_feedback_events_item
    ON feedback_events(item_kind, item_id);
CREATE INDEX IF NOT EXISTS idx_impressions_candidate_set
    ON impressions(candidate_set_id);
-- Additive annotations ABOUT feedback events (2026-08-26 closure): the
-- event row itself is never updated or deleted; an annotation marks it
-- excluded from evaluation (e.g. live-verification probes). Raw history
-- stays fully inspectable via the events table.
CREATE TABLE IF NOT EXISTS feedback_event_annotations (
    annotation_id TEXT PRIMARY KEY,
    feedback_event_id TEXT NOT NULL,
    annotation_type TEXT NOT NULL,    -- test_probe|operator_reviewed|...
    exclude_from_evaluation INTEGER NOT NULL DEFAULT 1,
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_event_annotations
    ON feedback_event_annotations(feedback_event_id, annotation_type);
-- Mutable workflow state, keyed by item; an event may cause a transition
-- but this table never replaces event history.
CREATE TABLE IF NOT EXISTS item_workflow_state (
    item_kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    state TEXT NOT NULL,              -- investigate|saved|acted|ignored
    prior_state TEXT,
    last_event_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (item_kind, item_id)
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
# Feedback capture
# ---------------------------------------------------------------------------
# record_feedback is the LEGACY write path (HTTP GET era). It is kept for
# import compatibility and historical reference; no route calls it anymore.
# New writes go through record_feedback_event below.

VERDICTS = frozenset((
    "useful", "known_already", "not_interested", "wrong_inference",
    "investigate", "acted_on", "save", "more_like", "less_like"))

# Feedback verdict -> workflow state transition. Verdicts absent from this
# map are pure evaluation signals (useful, known_already, wrong_inference,
# more_like, less_like) and must not move workflow state — clicks are not
# utility and preference signal is not a state change.
WORKFLOW_TRANSITIONS = {
    "investigate": "investigate",
    "save": "saved",
    "acted_on": "acted",
    "not_interested": "ignored",
}


def record_feedback(surface: str, item_kind: str, item_id: str,
                    verdict: str, note: str = "") -> bool:
    if verdict not in VERDICTS:
        return False
    conn = connect(_feedback_db(None))
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
    conn = connect(_feedback_db(None))
    try:
        rows = conn.execute(
            "SELECT verdict, COUNT(*) FROM feedback "
            "GROUP BY 1 ORDER BY 2 DESC").fetchall()
        return {v: n for v, n in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Impression + feedback-event contract (immutable events, separate state)
# ---------------------------------------------------------------------------

# Idempotency window for derived keys: a client retrying the same logical
# event within this window collapses to one row; distinct feedback moments
# (different verdict/note/item, or outside the window) stay separate.
IDEMPOTENCY_WINDOW_S = 60


def _event_idempotency_key(surface, item_kind, item_id, impression_id,
                           verdict, note, now_epoch: int) -> str:
    bucket = now_epoch // IDEMPOTENCY_WINDOW_S
    payload = "\x1f".join((surface, item_kind, item_id,
                           impression_id or "", verdict, note or "",
                           str(bucket)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_candidate_set(surface: str, ranking_policy: str,
                         ranking_policy_version: str, items, *,
                         provenance: str = "", db_path=None) -> dict:
    """Persist one surfacing batch: a candidate set + one impression row
    per item. Append-only; returns the ids the feedback UI must carry.

    items: ordered list of dicts with item_kind, item_id and optionally
    item_label, why_surfaced, score, origin_interest_id. rank_position is
    always the list position (1-based).
    """
    now = _now()
    candidate_set_id = "cs_" + uuid.uuid4().hex
    # Rank is ALWAYS the list position (1-based). Callers cannot override:
    # caller-supplied ranks could collide and make rank-order
    # reconstruction nondeterministic.
    ordered = [
        {"item_kind": it["item_kind"], "item_id": it["item_id"],
         "rank_position": pos, "why_surfaced": it.get("why_surfaced"),
         "item_label": it.get("item_label")}
        for pos, it in enumerate(items, start=1)]
    conn = connect(_feedback_db(db_path))
    try:
        conn.execute(
            "INSERT INTO candidate_sets (candidate_set_id, created_at, "
            "surface, ranking_policy, ranking_policy_version, items_json) "
            "VALUES (?,?,?,?,?,?)",
            (candidate_set_id, now, surface, ranking_policy,
             ranking_policy_version, json.dumps(ordered, ensure_ascii=False)))
        impression_ids = []
        for it, meta in zip(items, ordered):
            impression_id = "imp_" + uuid.uuid4().hex
            impression_ids.append(impression_id)
            conn.execute(
                "INSERT INTO impressions (impression_id, candidate_set_id, "
                "surfaced_at, surface, ranking_policy, "
                "ranking_policy_version, item_kind, item_id, item_label, "
                "rank_position, score, why_surfaced, origin_interest_id, "
                "actor_context, world_signal_json, "
                "personal_relevance_json, provenance, experiment_id, "
                "propensity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (impression_id, candidate_set_id, now, surface,
                 ranking_policy, ranking_policy_version, meta["item_kind"],
                 meta["item_id"], meta["item_label"],
                 meta["rank_position"],
                 it.get("score"), meta["why_surfaced"],
                 it.get("origin_interest_id"), it.get("actor_context"),
                 json.dumps(it["world_signal"]) if it.get("world_signal")
                 is not None else None,
                 json.dumps(it["personal_relevance"])
                 if it.get("personal_relevance") is not None else None,
                 provenance or None, it.get("experiment_id"),
                 it.get("propensity")))
        conn.commit()
        return {"candidate_set_id": candidate_set_id,
                "impression_ids": impression_ids}
    finally:
        conn.close()


def record_feedback_event(surface: str, item_kind: str, item_id: str,
                          verdict: str, *, note: str = "",
                          impression_id: str = None,
                          idempotency_key: str = None,
                          source_route: str = "unknown",
                          db_path=None) -> dict:
    """Append one immutable feedback event; transition workflow state
    atomically when the verdict maps to a state.

    Retries (same idempotency key, client-supplied or derived from a
    60-second bucket) return the existing event marked duplicate=True and
    cause no second state transition. Unknown impression_id is rejected.
    """
    if verdict not in VERDICTS:
        return {"ok": False, "error": "invalid verdict"}
    now_epoch = int(time.time())
    occurred_at = _now()
    event_id = "fe_" + uuid.uuid4().hex
    key = idempotency_key or _event_idempotency_key(
        surface, item_kind, item_id, impression_id, verdict, note,
        now_epoch)
    conn = connect(_feedback_db(db_path))
    try:
        if impression_id is not None:
            hit = conn.execute(
                "SELECT 1 FROM impressions WHERE impression_id = ?",
                (impression_id,)).fetchone()
            if hit is None:
                return {"ok": False, "error": "unknown impression_id"}
        try:
            conn.execute(
                "INSERT INTO feedback_events (feedback_event_id, "
                "impression_id, surface, item_kind, item_id, verdict, "
                "note, occurred_at, source_route, idempotency_key, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, impression_id, surface, item_kind, item_id,
                 verdict, note or None, occurred_at, source_route, key,
                 occurred_at))
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT feedback_event_id, surface, item_kind, item_id, "
                "verdict, note, impression_id FROM feedback_events "
                "WHERE idempotency_key = ?", (key,)).fetchone()
            conn.rollback()
            if row is None:
                return {"ok": False, "error": "idempotency conflict"}
            # Same key with different content is key REUSE, not a retry.
            if (row["surface"], row["item_kind"], row["item_id"],
                    row["verdict"], row["note"] or "",
                    row["impression_id"]) != (
                    surface, item_kind, item_id, verdict, note or "",
                    impression_id):
                return {"ok": False, "error": "idempotency key reuse "
                                              "with different payload"}
            return {"ok": True, "duplicate": True,
                    "feedback_event_id": row["feedback_event_id"],
                    "idempotency_key": key}
        target = WORKFLOW_TRANSITIONS.get(verdict)
        if target is not None:
            prior = conn.execute(
                "SELECT state FROM item_workflow_state "
                "WHERE item_kind = ? AND item_id = ?",
                (item_kind, item_id)).fetchone()
            conn.execute(
                "INSERT INTO item_workflow_state (item_kind, item_id, "
                "state, prior_state, last_event_id, updated_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(item_kind, item_id) DO UPDATE SET "
                "state=excluded.state, prior_state=excluded.prior_state, "
                "last_event_id=excluded.last_event_id, "
                "updated_at=excluded.updated_at",
                (item_kind, item_id, target,
                 prior[0] if prior else None, event_id, occurred_at))
        conn.commit()
        return {"ok": True, "duplicate": False,
                "feedback_event_id": event_id, "idempotency_key": key,
                "workflow_state": target}
    finally:
        conn.close()


def get_workflow_state(item_kind: str, item_id: str, db_path=None):
    conn = connect(_feedback_db(db_path))
    try:
        row = conn.execute(
            "SELECT state, prior_state, last_event_id, updated_at "
            "FROM item_workflow_state WHERE item_kind = ? AND item_id = ?",
            (item_kind, item_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_impressions_for_candidate_set(candidate_set_id: str, db_path=None):
    """Reconstruct a candidate set's impressions in rank order."""
    conn = connect(_feedback_db(db_path))
    try:
        rows = conn.execute(
            "SELECT * FROM impressions WHERE candidate_set_id = ? "
            "ORDER BY rank_position, impression_id",
            (candidate_set_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def annotate_feedback_event(feedback_event_id: str,
                            annotation_type: str = "test_probe",
                            *, exclude_from_evaluation: bool = True,
                            reason: str = "", db_path=None) -> dict:
    """Additively annotate an event (never mutate the event row).

    Unknown event ids are rejected. Retrying the same (event, type) with
    the same payload is idempotent; reuse with a different payload is
    reported as conflict. Exclusion only takes effect through the
    evaluation read (feedback_events_for_evaluation); raw history and
    workflow state are untouched.
    """
    annotation_id = "ann_" + uuid.uuid4().hex
    created_at = _now()
    conn = connect(_feedback_db(db_path))
    try:
        hit = conn.execute(
            "SELECT 1 FROM feedback_events WHERE feedback_event_id = ?",
            (feedback_event_id,)).fetchone()
        if hit is None:
            return {"ok": False, "error": "unknown feedback_event_id"}
        try:
            conn.execute(
                "INSERT INTO feedback_event_annotations (annotation_id, "
                "feedback_event_id, annotation_type, "
                "exclude_from_evaluation, reason, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (annotation_id, feedback_event_id, annotation_type,
                 1 if exclude_from_evaluation else 0, reason or None,
                 created_at))
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT annotation_id, exclude_from_evaluation, reason "
                "FROM feedback_event_annotations WHERE feedback_event_id=? "
                "AND annotation_type=?",
                (feedback_event_id, annotation_type)).fetchone()
            conn.rollback()
            if row is None:
                return {"ok": False, "error": "annotation conflict"}
            if (bool(row["exclude_from_evaluation"]),
                    row["reason"] or "") != (
                    bool(exclude_from_evaluation), reason or ""):
                return {"ok": False, "error": "annotation key reuse with "
                                              "different payload"}
            return {"ok": True, "duplicate": True,
                    "annotation_id": row["annotation_id"]}
        conn.commit()
        return {"ok": True, "duplicate": False,
                "annotation_id": annotation_id}
    finally:
        conn.close()


def get_feedback_event(feedback_event_id: str, db_path=None):
    """Raw single-event read for audit — annotations do not hide it."""
    conn = connect(_feedback_db(db_path))
    try:
        row = conn.execute(
            "SELECT * FROM feedback_events WHERE feedback_event_id = ?",
            (feedback_event_id,)).fetchone()
        if row is None:
            return None
        anns = [dict(a) for a in conn.execute(
            "SELECT * FROM feedback_event_annotations "
            "WHERE feedback_event_id = ?", (feedback_event_id,))]
        return dict(row) | {"annotations": anns}
    finally:
        conn.close()


# Canonical evaluation read: excludes events annotated
# exclude_from_evaluation=1 by default; include_excluded=True is the
# explicit audit mode (annotated events come back flagged, not filtered).
def feedback_events_for_evaluation(item_kind: str = None,
                                   item_id: str = None,
                                   impression_id: str = None,
                                   include_excluded: bool = False,
                                   db_path=None) -> list:
    clauses, args = [], []
    if item_kind is not None:
        clauses.append("e.item_kind = ?")
        args.append(item_kind)
    if item_id is not None:
        clauses.append("e.item_id = ?")
        args.append(item_id)
    if impression_id is not None:
        clauses.append("e.impression_id = ?")
        args.append(impression_id)
    if not include_excluded:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM feedback_event_annotations a "
            "WHERE a.feedback_event_id = e.feedback_event_id AND "
            "a.exclude_from_evaluation = 1)")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = connect(_feedback_db(db_path))
    try:
        rows = conn.execute(
            "SELECT e.*" + (", EXISTS ("
                            "SELECT 1 FROM feedback_event_annotations a "
                            "WHERE a.feedback_event_id="
                            "e.feedback_event_id AND "
                            "a.exclude_from_evaluation=1) AS excluded"
                            if include_excluded else "") +
            f" FROM feedback_events e{where} "
            "ORDER BY e.occurred_at, e.feedback_event_id", args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
