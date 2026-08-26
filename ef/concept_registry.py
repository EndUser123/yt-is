"""Durable open-world Concept Registry for the Evidence Fabric catalog.

Durable memory is not durable attention: concepts are WORLD objects whose
identity is stable and deterministic, while lifecycle and user-relationship
state are mutable attention signals. Nothing is ever deleted when attention
decays; concepts cool to 'dormant'/'obsolete' and remain queryable. Concepts
are distinct from user Interests — links between them are explicit rows, never
a collapse of the two axes (lifecycle_state x user_relationship are
independent).

Registry lives as additive tables in the existing catalog DB; ensure_schema is
idempotent and safe alongside other tables.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")

LIFECYCLE_STATES = (
    "candidate",
    "emerging",
    "active",
    "durable",
    "cooling",
    "dormant",
    "obsolete",
)
USER_RELATIONSHIPS = (
    "unknown",
    "adjacent",
    "monitoring",
    "learning",
    "active_project",
    "durable_interest",
    "rejected",
)
REGISTRY_SCHEMA_VERSION = "concept-registry-v1"

# Mechanical (non-operator) inference may only reach 'adjacent'.
_MECHANICAL_CAP = {"adjacent"}
_STRONG_USER_STATES = {"active_project", "durable_interest", "rejected"}
_OPERATOR_LIKE_METHODS = ("operator", "strong_user_state")
_LINK_METHODS = ("shared_cluster", "semantic", "llm", "operator")
_EPISODE_CLOSE_STATES = ("closed", "cooled")

SCHEMA = """
CREATE TABLE IF NOT EXISTS concepts (
  concept_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  concept_type TEXT NOT NULL,
  first_seen TEXT,
  last_seen TEXT,
  discovered_at TEXT,
  lifecycle_state TEXT NOT NULL,
  user_relationship TEXT NOT NULL,
  world_signal_score REAL,
  personal_relevance_score REAL,
  evidence_count INTEGER,
  source_diversity INTEGER,
  updated_at TEXT,
  metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS concept_aliases (
  concept_id TEXT,
  alias TEXT,
  normalized_alias TEXT,
  created_at TEXT,
  UNIQUE(normalized_alias, concept_id)
);
CREATE TABLE IF NOT EXISTS concept_observations (
  observation_id TEXT PRIMARY KEY,
  concept_id TEXT,
  source_kind TEXT,
  source_id TEXT,
  source_url TEXT,
  source_author TEXT,
  observed_at TEXT,
  title TEXT,
  snippet TEXT,
  evidence_ref TEXT,
  discovery_run_id TEXT,
  metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS trend_episodes (
  episode_id TEXT PRIMARY KEY,
  concept_id TEXT,
  started_at TEXT,
  last_active_at TEXT,
  peak_at TEXT,
  ended_at TEXT,
  state TEXT,
  recent_rate REAL,
  baseline_rate REAL,
  acceleration REAL,
  source_diversity INTEGER,
  independent_source_count INTEGER,
  novelty_score REAL,
  evidence_json TEXT,
  policy_version TEXT
);
CREATE TABLE IF NOT EXISTS concept_relations (
  src_concept_id TEXT,
  dst_concept_id TEXT,
  relation TEXT,
  confidence REAL,
  method TEXT,
  evidence_json TEXT,
  created_at TEXT,
  updated_at TEXT,
  UNIQUE(src_concept_id, dst_concept_id, relation)
);
CREATE TABLE IF NOT EXISTS concept_interest_links (
  concept_id TEXT,
  interest_id TEXT,
  relation TEXT,
  method TEXT,
  provenance_json TEXT,
  created_at TEXT,
  UNIQUE(concept_id, interest_id, method)
);
CREATE TABLE IF NOT EXISTS concept_state_events (
  event_id TEXT PRIMARY KEY,
  concept_id TEXT,
  field TEXT,
  old_value TEXT,
  new_value TEXT,
  reason TEXT,
  method TEXT,
  discovery_run_id TEXT,
  ts TEXT
);
CREATE TABLE IF NOT EXISTS discovery_runs (
  run_id TEXT PRIMARY KEY,
  run_kind TEXT,
  policy_version TEXT,
  as_of TEXT,
  started_at TEXT,
  completed_at TEXT,
  status TEXT,
  input_summary_json TEXT,
  output_summary_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_concepts_lifecycle ON concepts(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_aliases_norm ON concept_aliases(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_observations_concept ON concept_observations(concept_id);
CREATE INDEX IF NOT EXISTS idx_episodes_concept ON trend_episodes(concept_id);
"""


class RegistryError(ValueError):
    """Raised for invalid registry values or illegal transitions."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _short_digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _norm_identity(s: str) -> str:
    return _collapse_ws(s.casefold())


def concept_identity_id(concept_type: str, canonical_name: str) -> str:
    """Deterministic identity: 'concept_' + sha256(norm(type) US norm(name))[:16]."""
    return "concept_" + _short_digest(_norm_identity(concept_type), _norm_identity(canonical_name))


_PUNCT_BETWEEN = re.compile(r"(?<=[\w])[-_](?=[\w])")
_STRIP_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")


def normalize_alias(alias: str) -> str:
    """Character-level normalization only: casefold, collapse whitespace,
    hyphens/underscores between word chars -> single space, strip surrounding
    punctuation. Conservative by design: never merges on token overlap."""
    s = alias.casefold()
    s = _PUNCT_BETWEEN.sub(" ", s)
    s = _collapse_ws(s)
    s = _STRIP_PUNCT.sub("", s)
    return _collapse_ws(s)


def connect(db_path: Any = None) -> sqlite3.Connection:
    """Open (and schema-ensure) the registry DB. row_factory=Row, 30s busy timeout."""
    from csf.db_utils import open_sqlite_rw

    target_path = db_path if db_path is not None else get_catalog_path()
    conn = open_sqlite_rw(target_path, timeout=30.0, wal=False)
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create registry tables; safe on a DB holding other tables."""
    conn.executescript(SCHEMA)
    conn.commit()


def _dump(obj: Any) -> str | None:
    return json.dumps(obj, ensure_ascii=False) if obj is not None else None


def upsert_concept(
    conn: sqlite3.Connection,
    canonical_name: str,
    concept_type: str,
    *,
    first_seen: str | None = None,
    last_seen: str | None = None,
    lifecycle_state: str = "candidate",
    user_relationship: str = "unknown",
    world_signal_score: float | None = None,
    personal_relevance_score: float | None = None,
    metadata: dict | None = None,
    run_id: str | None = None,
) -> str:
    """Create or update a concept. Identity is durable; attention is mutable;
    never deletes. first_seen/discovered_at are set once and not overwritten."""
    if lifecycle_state not in LIFECYCLE_STATES:
        raise RegistryError(f"invalid lifecycle_state: {lifecycle_state!r}")
    if user_relationship not in USER_RELATIONSHIPS:
        raise RegistryError(f"invalid user_relationship: {user_relationship!r}")
    concept_id = concept_identity_id(concept_type, canonical_name)
    now = _now()
    existing = conn.execute(
        "SELECT concept_id FROM concepts WHERE concept_id = ?", (concept_id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO concepts (concept_id, canonical_name, concept_type,
               first_seen, last_seen, discovered_at, lifecycle_state,
               user_relationship, world_signal_score, personal_relevance_score,
               evidence_count, source_diversity, updated_at, metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,0,?,?)""",
            (
                concept_id,
                canonical_name,
                concept_type,
                first_seen or now,
                last_seen,
                now,
                lifecycle_state,
                user_relationship,
                world_signal_score,
                personal_relevance_score,
                now,
                _dump(metadata),
            ),
        )
    else:
        conn.execute(
            """UPDATE concepts SET canonical_name = ?, concept_type = ?,
               last_seen = COALESCE(?, last_seen), lifecycle_state = ?,
               user_relationship = ?,
               world_signal_score = COALESCE(?, world_signal_score),
               personal_relevance_score = COALESCE(?, personal_relevance_score),
               metadata_json = COALESCE(?, metadata_json), updated_at = ?
               WHERE concept_id = ?""",
            (
                canonical_name,
                concept_type,
                last_seen,
                lifecycle_state,
                user_relationship,
                world_signal_score,
                personal_relevance_score,
                _dump(metadata),
                now,
                concept_id,
            ),
        )
    conn.commit()
    return concept_id


def add_alias(conn: sqlite3.Connection, concept_id: str, alias: str) -> bool:
    """Add an alias; True if inserted, False if it already existed."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO concept_aliases (concept_id, alias, normalized_alias, created_at)"
        " VALUES (?,?,?,?)",
        (concept_id, alias, normalize_alias(alias), _now()),
    )
    conn.commit()
    return cur.rowcount > 0


def resolve_alias(conn: sqlite3.Connection, alias: str) -> str | None:
    """Exact normalized match only; no fuzzy matching, ever."""
    row = conn.execute(
        "SELECT concept_id FROM concept_aliases WHERE normalized_alias = ?"
        " ORDER BY concept_id LIMIT 1",
        (normalize_alias(alias),),
    ).fetchone()
    return row["concept_id"] if row else None


def _append_event(
    conn: sqlite3.Connection,
    concept_id: str,
    field: str,
    old_value: str | None,
    new_value: str,
    reason: str,
    method: str | None,
    run_id: str | None,
) -> None:
    ts = _now()
    # The digest alone is not unique: two transitions of the same field to
    # the same value within one second collide on the PK and fail the
    # INSERT. Events are append-only receipts, not replay-deduped, so a
    # random suffix is safe here (unlike observation/episode ids).
    event_id = "evt_" + _short_digest(concept_id, field, new_value, ts) + uuid.uuid4().hex[:8]
    conn.execute(
        "INSERT INTO concept_state_events (event_id, concept_id, field, old_value,"
        " new_value, reason, method, discovery_run_id, ts) VALUES (?,?,?,?,?,?,?,?,?)",
        (event_id, concept_id, field, old_value, new_value, reason, method, run_id, ts),
    )


def merge_concepts(
    conn: sqlite3.Connection,
    survivor_id: str,
    merged_id: str,
    run_id: str | None = None,
) -> None:
    """Explicit-only merge: move aliases/observations/episodes/links/relations
    to the survivor, mark the merged concept lifecycle='obsolete' with a state
    event. Rows that would collide with an existing survivor row (same
    normalized alias, same interest link, same relation edge) are dropped —
    the survivor already covers them. Refuses self-merge."""
    if survivor_id == merged_id:
        raise RegistryError("cannot merge a concept with itself")
    for cid in (survivor_id, merged_id):
        if conn.execute("SELECT 1 FROM concepts WHERE concept_id = ?", (cid,)).fetchone() is None:
            raise RegistryError(f"unknown concept: {cid}")
    # OR IGNORE keeps the merge alive when the survivor already holds the
    # same key; whatever remains attached to merged_id afterwards is a
    # duplicate the survivor covers, dropped instead of left dangling on an
    # obsolete concept.
    conn.execute(
        "UPDATE OR IGNORE concept_aliases SET concept_id = ? WHERE concept_id = ?",
        (survivor_id, merged_id),
    )
    conn.execute("DELETE FROM concept_aliases WHERE concept_id = ?", (merged_id,))
    conn.execute(
        "UPDATE OR IGNORE concept_interest_links SET concept_id = ? WHERE concept_id = ?",
        (survivor_id, merged_id),
    )
    conn.execute("DELETE FROM concept_interest_links WHERE concept_id = ?", (merged_id,))
    # Relations move on both edge directions; a merged->survivor edge becomes
    # a survivor self-loop, so those are removed after the move.
    conn.execute(
        "UPDATE OR IGNORE concept_relations SET src_concept_id = ? WHERE src_concept_id = ?",
        (survivor_id, merged_id),
    )
    conn.execute(
        "UPDATE OR IGNORE concept_relations SET dst_concept_id = ? WHERE dst_concept_id = ?",
        (survivor_id, merged_id),
    )
    conn.execute("DELETE FROM concept_relations WHERE src_concept_id = ? OR dst_concept_id = ?",
                 (merged_id, merged_id))
    conn.execute("DELETE FROM concept_relations WHERE src_concept_id = ? AND dst_concept_id = ?",
                 (survivor_id, survivor_id))
    conn.execute(
        "UPDATE concept_observations SET concept_id = ? WHERE concept_id = ?",
        (survivor_id, merged_id),
    )
    conn.execute(
        "UPDATE trend_episodes SET concept_id = ? WHERE concept_id = ?", (survivor_id, merged_id)
    )
    old = conn.execute(
        "SELECT lifecycle_state FROM concepts WHERE concept_id = ?", (merged_id,)
    ).fetchone()["lifecycle_state"]
    conn.execute(
        "UPDATE concepts SET lifecycle_state = 'obsolete', updated_at = ? WHERE concept_id = ?",
        (_now(), merged_id),
    )
    _append_event(
        conn, merged_id, "lifecycle_state", old, "obsolete",
        f"merged into {survivor_id}", "operator", run_id,
    )
    _refresh_counts(conn, survivor_id)
    conn.commit()


def _refresh_counts(conn: sqlite3.Connection, concept_id: str) -> None:
    row = conn.execute(
        """SELECT COUNT(*) AS n, COUNT(DISTINCT source_kind) AS d,
           MAX(observed_at) AS last FROM concept_observations WHERE concept_id = ?""",
        (concept_id,),
    ).fetchone()
    conn.execute(
        "UPDATE concepts SET evidence_count = ?, source_diversity = ?,"
        " last_seen = COALESCE(?, last_seen), updated_at = ? WHERE concept_id = ?",
        (row["n"], row["d"], row["last"], _now(), concept_id),
    )


def record_observation(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    source_kind: str,
    source_id: str,
    source_url: str | None = None,
    source_author: str | None = None,
    observed_at: str,
    title: str | None = None,
    snippet: str | None = None,
    evidence_ref: str | None = None,
    run_id: str | None = None,
    metadata: dict | None = None,
) -> str:
    """Idempotent observation insert; replay returns the same observation_id
    and refreshes last_seen (forward only), evidence_count, source_diversity."""
    observation_id = "obs_" + _short_digest(
        concept_id, source_kind, source_id, observed_at, title or ""
    )
    conn.execute(
        "INSERT OR IGNORE INTO concept_observations (observation_id, concept_id,"
        " source_kind, source_id, source_url, source_author, observed_at, title,"
        " snippet, evidence_ref, discovery_run_id, metadata_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            observation_id,
            concept_id,
            source_kind,
            source_id,
            source_url,
            source_author,
            observed_at,
            title,
            snippet,
            evidence_ref,
            run_id,
            _dump(metadata),
        ),
    )
    _refresh_counts(conn, concept_id)
    conn.commit()
    return observation_id


def open_trend_episode(
    conn: sqlite3.Connection,
    concept_id: str,
    *,
    started_at: str,
    baseline_rate: float,
    policy_version: str,
    evidence: Any = None,
) -> str:
    """Open an active trend episode; prior episodes for the concept are preserved."""
    episode_id = "ep_" + _short_digest(concept_id, started_at, policy_version)
    conn.execute(
        "INSERT OR IGNORE INTO trend_episodes (episode_id, concept_id, started_at,"
        " last_active_at, peak_at, ended_at, state, recent_rate, baseline_rate,"
        " acceleration, source_diversity, independent_source_count, novelty_score,"
        " evidence_json, policy_version) VALUES (?,?,?,?,NULL,NULL,'active',NULL,?,"
        "NULL,NULL,NULL,NULL,?,?)",
        (episode_id, concept_id, started_at, started_at, baseline_rate, _dump(evidence), policy_version),
    )
    conn.commit()
    return episode_id


def update_trend_episode(
    conn: sqlite3.Connection,
    episode_id: str,
    *,
    recent_rate: float | None = None,
    acceleration: float | None = None,
    source_diversity: int | None = None,
    independent_source_count: int | None = None,
    novelty_score: float | None = None,
    last_active_at: str | None = None,
    peak_at: str | None = None,
    evidence: Any = None,
) -> None:
    """Mutable attention update; only provided fields are changed."""
    sets, params = [], []
    if recent_rate is not None:
        sets.append("recent_rate = ?"); params.append(recent_rate)
    if acceleration is not None:
        sets.append("acceleration = ?"); params.append(acceleration)
    if source_diversity is not None:
        sets.append("source_diversity = ?"); params.append(source_diversity)
    if independent_source_count is not None:
        sets.append("independent_source_count = ?"); params.append(independent_source_count)
    if novelty_score is not None:
        sets.append("novelty_score = ?"); params.append(novelty_score)
    if last_active_at is not None:
        sets.append("last_active_at = ?"); params.append(last_active_at)
    if peak_at is not None:
        sets.append("peak_at = ?"); params.append(peak_at)
    if evidence is not None:
        sets.append("evidence_json = ?"); params.append(_dump(evidence))
    if not sets:
        return
    params.append(episode_id)
    conn.execute(f"UPDATE trend_episodes SET {', '.join(sets)} WHERE episode_id = ?", params)
    conn.commit()


def close_trend_episode(
    conn: sqlite3.Connection,
    episode_id: str,
    *,
    ended_at: str,
    state: str = "closed",
) -> None:
    """Close an episode (state closed|cooled). The row is preserved, never deleted."""
    if state not in _EPISODE_CLOSE_STATES:
        raise RegistryError(f"invalid close state: {state!r}")
    conn.execute(
        "UPDATE trend_episodes SET ended_at = ?, state = ? WHERE episode_id = ?",
        (ended_at, state, episode_id),
    )
    conn.commit()


def active_episode(conn: sqlite3.Connection, concept_id: str) -> sqlite3.Row | None:
    """The currently active episode for a concept, if any."""
    return conn.execute(
        "SELECT * FROM trend_episodes WHERE concept_id = ? AND state = 'active'"
        " ORDER BY started_at DESC LIMIT 1",
        (concept_id,),
    ).fetchone()


def set_lifecycle(
    conn: sqlite3.Connection,
    concept_id: str,
    new_state: str,
    *,
    reason: str,
    run_id: str | None = None,
) -> None:
    """Transition lifecycle_state; no-op if unchanged, otherwise append a
    state-event receipt. Never deletes the concept row."""
    if new_state not in LIFECYCLE_STATES:
        raise RegistryError(f"invalid lifecycle_state: {new_state!r}")
    row = conn.execute(
        "SELECT lifecycle_state FROM concepts WHERE concept_id = ?", (concept_id,)
    ).fetchone()
    if row is None:
        raise RegistryError(f"unknown concept: {concept_id}")
    old = row["lifecycle_state"]
    if old == new_state:
        return
    conn.execute(
        "UPDATE concepts SET lifecycle_state = ?, updated_at = ? WHERE concept_id = ?",
        (new_state, _now(), concept_id),
    )
    _append_event(conn, concept_id, "lifecycle_state", old, new_state, reason, None, run_id)
    conn.commit()


def set_user_relationship(
    conn: sqlite3.Connection,
    concept_id: str,
    new_rel: str,
    *,
    reason: str,
    method: str,
    run_id: str | None = None,
) -> None:
    """Transition user_relationship with authority rules: mechanical inference
    (shared_cluster|semantic|llm) may only reach 'adjacent'; strong user states
    (active_project/durable_interest/rejected) require operator-grade method."""
    if new_rel not in USER_RELATIONSHIPS:
        raise RegistryError(f"invalid user_relationship: {new_rel!r}")
    row = conn.execute(
        "SELECT user_relationship FROM concepts WHERE concept_id = ?", (concept_id,)
    ).fetchone()
    if row is None:
        raise RegistryError(f"unknown concept: {concept_id}")
    old = row["user_relationship"]
    if new_rel in _MECHANICAL_CAP and method not in ("shared_cluster", "semantic", "llm"):
        raise RegistryError(f"method {method!r} cannot set relationship {new_rel!r}")
    if new_rel in _STRONG_USER_STATES and method not in _OPERATOR_LIKE_METHODS:
        raise RegistryError(
            f"user_relationship {new_rel!r} requires operator-grade method, got {method!r}"
        )
    if old == new_rel:
        return
    conn.execute(
        "UPDATE concepts SET user_relationship = ?, updated_at = ? WHERE concept_id = ?",
        (new_rel, _now(), concept_id),
    )
    _append_event(conn, concept_id, "user_relationship", old, new_rel, reason, method, run_id)
    conn.commit()


def record_concept_relation(
    conn: sqlite3.Connection,
    src_concept_id: str,
    dst_concept_id: str,
    relation: str,
    confidence: float,
    method: str,
    evidence: Any = None,
) -> None:
    """UNIQUE-keyed upsert; replay does not duplicate rows."""
    now = _now()
    conn.execute(
        """INSERT INTO concept_relations (src_concept_id, dst_concept_id, relation,
           confidence, method, evidence_json, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(src_concept_id, dst_concept_id, relation) DO UPDATE SET
             confidence = excluded.confidence,
             method = excluded.method,
             evidence_json = excluded.evidence_json,
             updated_at = excluded.updated_at""",
        (src_concept_id, dst_concept_id, relation, confidence, method, _dump(evidence), now, now),
    )
    conn.commit()


def link_concept_interest(
    conn: sqlite3.Connection,
    concept_id: str,
    interest_id: str,
    *,
    method: str,
    provenance: Any = None,
) -> None:
    """Upsert a concept->interest link (relation 'relevant_to')."""
    if method not in _LINK_METHODS:
        raise RegistryError(f"invalid link method: {method!r}")
    conn.execute(
        """INSERT INTO concept_interest_links (concept_id, interest_id, relation,
           method, provenance_json, created_at) VALUES (?, ?, 'relevant_to', ?, ?, ?)
           ON CONFLICT(concept_id, interest_id, method) DO UPDATE SET
             relation = excluded.relation,
             provenance_json = excluded.provenance_json""",
        (concept_id, interest_id, method, _dump(provenance), _now()),
    )
    conn.commit()


def record_discovery_run(
    conn: sqlite3.Connection,
    run_id: str,
    run_kind: str,
    policy_version: str,
    as_of: str,
    status: str = "running",
    input_summary: Any = None,
    output_summary: Any = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO discovery_runs (run_id, run_kind, policy_version,"
        " as_of, started_at, completed_at, status, input_summary_json, output_summary_json)"
        " VALUES (?,?,?,?,?,NULL,?,?,NULL)"
        " ON CONFLICT(run_id) DO UPDATE SET status = excluded.status,"
        " input_summary_json = excluded.input_summary_json",
        (run_id, run_kind, policy_version, as_of, _now(), status, _dump(input_summary)),
    )
    conn.commit()


def complete_discovery_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    output_summary: Any = None,
) -> None:
    conn.execute(
        "UPDATE discovery_runs SET status = ?, completed_at = ?, output_summary_json = ?"
        " WHERE run_id = ?",
        (status, _now(), _dump(output_summary), run_id),
    )
    conn.commit()


def get_concept(conn: sqlite3.Connection, concept_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM concepts WHERE concept_id = ?", (concept_id,)).fetchone()


def list_concepts(
    conn: sqlite3.Connection,
    lifecycle: str | None = None,
    user_relationship: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM concepts"
    conds, params = [], []
    if lifecycle is not None:
        conds.append("lifecycle_state = ?"); params.append(lifecycle)
    if user_relationship is not None:
        conds.append("user_relationship = ?"); params.append(user_relationship)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY concept_id LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def observation_counts(conn: sqlite3.Connection, concept_id: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT source_kind) AS d"
        " FROM concept_observations WHERE concept_id = ?",
        (concept_id,),
    ).fetchone()
    return {"total": row["n"], "distinct_source_kinds": row["d"]}


_RADAR_TIER = {
    "emerging": 0,
    "active": 0,
    "candidate": 1,
    "durable": 2,
    "cooling": 3,
    "dormant": 3,
    "obsolete": 4,
}


def discovery_radar(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    """Stable, read-only dashboard model. Ranked by attention only
    (emerging/active first by world_signal desc, then candidate, then
    cooling/dormant; tiebreak concept_id). Never mutates any table."""
    rows = conn.execute("SELECT * FROM concepts").fetchall()
    items = []
    for row in rows:
        ep = active_episode(conn, row["concept_id"])
        last_evt = conn.execute(
            "SELECT reason FROM concept_state_events WHERE concept_id = ?"
            " ORDER BY ts DESC, rowid DESC LIMIT 1",
            (row["concept_id"],),
        ).fetchone()
        obs_meta = conn.execute(
            "SELECT metadata_json FROM concept_observations WHERE concept_id = ?"
            " AND metadata_json IS NOT NULL",
            (row["concept_id"],),
        ).fetchall()
        methods: list[str] = []
        for om in obs_meta:
            try:
                m = json.loads(om["metadata_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(m, dict) and m.get("discovery_method"):
                methods.append(str(m["discovery_method"]))
        related = [
            r["interest_id"]
            for r in conn.execute(
                "SELECT DISTINCT interest_id FROM concept_interest_links WHERE concept_id = ?",
                (row["concept_id"],),
            )
        ]
        meta: dict = {}
        if row["metadata_json"]:
            try:
                loaded = json.loads(row["metadata_json"])
                if isinstance(loaded, dict):
                    meta = loaded
            except (TypeError, ValueError):
                meta = {}
        why = ([last_evt["reason"]] if last_evt and last_evt["reason"] else []) + methods
        items.append(
            {
                "concept_id": row["concept_id"],
                "name": row["canonical_name"],
                "type": row["concept_type"],
                "lifecycle": row["lifecycle_state"],
                "user_relationship": row["user_relationship"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "world_signal_score": row["world_signal_score"],
                "personal_relevance_score": row["personal_relevance_score"],
                "acceleration": ep["acceleration"] if ep else None,
                "novelty_score": ep["novelty_score"] if ep else None,
                "episode_state": ep["state"] if ep else None,
                "source_diversity": row["source_diversity"],
                "why_surfaced": why,
                "related_interests": related,
                "novelty_flags": meta.get("novelty_flags", []),
            }
        )
    items.sort(
        key=lambda c: (
            _RADAR_TIER.get(c["lifecycle"], 5),
            -(c["world_signal_score"] or 0.0),
            c["concept_id"],
        )
    )
    return items[:limit]
