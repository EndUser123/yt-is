"""Typed personal-graph tables + feedback capture.

The v2 schema from docs/design/personal-intelligence-system-2026-08-24.md.
Relational tables in catalog.sqlite — NO graph database (the design's
explicit decision: a graph data model is justified before a graph DB).

Tables are created idempotently on first use. The feedback endpoint is
live the moment the service routes it — the operator's directive:
deferred feedback = lost ground-truth history.
"""

from __future__ import annotations

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
CREATE INDEX IF NOT EXISTS idx_interests_parent
    ON interests(parent_id);
CREATE INDEX IF NOT EXISTS idx_feedback_surface
    ON feedback(surface, item_kind);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CATALOG), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


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
