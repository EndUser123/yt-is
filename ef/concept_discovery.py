"""Open-world internal concept discovery over EXISTING Evidence Fabric data.

Open-world premise: unknown vocabulary is an INPUT, not an error. Discovery
never requires a predeclared concept name — concepts are derived from what
the evidence graph actually contains. Popularity alone is insufficient: a
burst must show absolute support AND independent-source diversity before it
can become 'emerging'. A 50-mention single-channel spike stays a candidate.

Two mechanical signals (no provider calls):
  1. NEW ENTITY  — kg entity nodes whose evidence is recent/bursting.
  2. SEMANTIC CLUSTER — recently appearing evidence clusters
     (ef.evidence_clusters.evidence_cluster_inventory), entering as
     'candidate' only in v1.

Historical replay: every computation obeys the ``--as-of`` cutoff. No
observation dated after ``as_of`` may influence first_seen, windows, counts,
novelty, or lifecycle. All registry ids (concept, observation, episode) are
deterministic, so identical scans are idempotent.

POLICY / POLICY_VERSION: the windows, floors, and weights below are INITIAL
policy values (burst-policy-v1), chosen for mechanical defensibility — NOT
tuned optima. Changes to them require a new POLICY_VERSION and replay.

burst-policy-v2 (SHADOW, calibrated 2026-08-25 on consumed holdout-v4 as
TRAINING_DIAGNOSTIC_ONLY; architect-approved): Gamma-Poisson rate-change
signal + persistence episodes via ef/burst_policy_v2.py. It is explicitly
selectable via scan_internal(policy_version="burst-policy-v2") but is NOT
the default until it passes a completely new unseen formal holdout. The
formal evaluator must pin the policy version from its freeze receipt,
never rely on this module's default.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ef import burst_policy_v2 as bp2
from ef import concept_registry as cr
from ef.evidence_clusters import evidence_cluster_inventory

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")

POLICY_VERSION = "burst-policy-v1"

SOURCE_LABELS = {
    "notebooklm": "youtube",
    "ytdlp": "youtube",
    "selenium": "youtube",
    "whisper": "youtube",
    "hackernews": "hn",
}

# INITIAL policy values (burst-policy-v1), not optimal. Windows in days,
# floors are absolute (never raw-percentage-only), weights sum to 1.0.
POLICY = {
    "recent_window_days": 30,
    "baseline_window_days": 90,      # ends where the recent window begins
    "min_recent_count": 4,           # absolute support floor for 'emerging'
    "min_ratio": 2.0,                # smoothed rate ratio floor
    "min_independent_channels": 3,
    "min_source_types": 2,
    "novelty_first_seen_days": 90,   # first_seen younger than this -> novelty 1.0
    "candidate_min_recent": 2,       # below this: not even an entity candidate
    "weights": {
        "support": 0.35,
        "acceleration": 0.25,
        "diversity": 0.20,
        "persistence": 0.10,
        "novelty": 0.10,
    },
}

_W = POLICY["weights"]


def _catalog_ro(catalog_path: Any = None) -> sqlite3.Connection:
    path = CATALOG if catalog_path is None else Path(catalog_path)
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _world_signal(stats: dict, as_of_d: date) -> float:
    """INITIAL v1 scoring formula (documented, versioned — not optimal):

        support      = min(recent_count / 10, 1)
        acceleration = min(log2(smoothed_ratio) / 3, 1), clipped at >= 0
        diversity    = min((channels / 5 + source_types / 3) / 2, 1)
        persistence  = min(active_months / 6, 1)
        novelty      = 1.0 if first_seen within novelty_first_seen_days of
                       as_of else 0.0
        world_signal = sum(weights[k] * component_k)
    """
    import math

    ratio = stats["smoothed_ratio"]
    accel = max(min(math.log2(ratio) / 3.0, 1.0), 0.0) if ratio > 0 else 0.0
    first = date.fromisoformat(stats["first_seen"])
    novelty = 1.0 if (as_of_d - first).days <= POLICY["novelty_first_seen_days"] else 0.0
    return (
        _W["support"] * min(stats["recent_count"] / 10.0, 1.0)
        + _W["acceleration"] * accel
        + _W["diversity"]
        * min(
            (stats["channels"] / 5.0 + stats["source_types"] / 3.0) / 2.0,
            1.0,
        )
        + _W["persistence"] * min(stats["active_months"] / 6.0, 1.0)
        + _W["novelty"] * novelty
    )


def _is_emerging(stats: dict) -> bool:
    """Mechanical, versioned promotion: absolute support floor + smoothed
    ratio floor + independent channel floor + source-type floor. All four
    must hold; raw percentage growth alone can never promote."""
    return (
        stats["recent_count"] >= POLICY["min_recent_count"]
        and stats["smoothed_ratio"] >= POLICY["min_ratio"]
        and stats["channels"] >= POLICY["min_independent_channels"]
        and stats["source_types"] >= POLICY["min_source_types"]
    )


def _entity_observations(conn: sqlite3.Connection, as_of: str) -> dict[str, list[dict]]:
    """All entity observations at or before as_of, keyed by node_id.
    Authoritative observation date = substr(COALESCE(NULLIF(published_at,''),
    captured_at),1,10) — published_at wins whenever present."""
    rows = conn.execute(
        r"""
        SELECT m.src_id AS node_id, n.label AS label, eu.eu_id AS eu_id,
               eu.video_id AS video_id, eu.channel_id AS channel_id,
               eu.source AS source,
               substr(COALESCE(NULLIF(eu.published_at,''), eu.captured_at),1,10)
                 AS obs_date
        FROM kg_edges m
        JOIN kg_nodes n ON n.node_id = m.src_id AND n.kind = 'entity'
        JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
        WHERE m.relation = 'mentioned_in'
          AND substr(COALESCE(NULLIF(eu.published_at,''), eu.captured_at),1,10) != ''
          AND substr(COALESCE(NULLIF(eu.published_at,''), eu.captured_at),1,10) <= ?
        """,
        (as_of,),
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["node_id"] if isinstance(r, sqlite3.Row) else r[0], []).append(
            {
                "label": r["label"],
                "eu_id": r["eu_id"],
                "video_id": r["video_id"],
                "channel_id": r["channel_id"],
                "source": r["source"],
                "obs_date": r["obs_date"],
            }
        )
    return out


def _stats_for(obs: list[dict], as_of_d: date) -> dict:
    recent_start = as_of_d - timedelta(days=POLICY["recent_window_days"])
    baseline_end = recent_start
    baseline_start = baseline_end - timedelta(days=POLICY["baseline_window_days"])
    recent = [o for o in obs if recent_start < date.fromisoformat(o["obs_date"]) <= as_of_d]
    baseline = [
        o for o in obs if baseline_start < date.fromisoformat(o["obs_date"]) <= baseline_end
    ]
    recent_count = len(recent)
    baseline_count = len(baseline)
    channels = len({o["channel_id"] for o in recent})
    source_types = len({SOURCE_LABELS.get(o["source"], o["source"]) for o in recent})
    active_months = len({o["obs_date"][:7] for o in obs})
    first_seen = min(o["obs_date"] for o in obs)
    last_seen = max(o["obs_date"] for o in obs)
    smoothed_ratio = (recent_count + 1) / (baseline_count + 1)
    return {
        "recent_count": recent_count,
        "baseline_count": baseline_count,
        "channels": channels,
        "source_types": source_types,
        "active_months": active_months,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "recent_start": recent_start.isoformat(),
        "smoothed_ratio": smoothed_ratio,
        "recent_video_ids": sorted({o["video_id"] for o in recent}),
    }


def _existing_state(conn: sqlite3.Connection, concept_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM concepts WHERE concept_id = ?", (concept_id,)
    ).fetchone()


def _preserve_upsert(
    conn: sqlite3.Connection,
    label: str,
    concept_type: str,
    *,
    first_seen: str,
    last_seen: str,
    lifecycle_state: str,
    world_signal_score: float | None,
    metadata: dict,
) -> str:
    """upsert_concept while preserving user_relationship and
    personal_relevance_score of an existing row (upsert overwrites them)."""
    concept_id = cr.concept_identity_id(concept_type, label)
    row = _existing_state(conn, concept_id)
    rel = row["user_relationship"] if row else "unknown"
    prs = row["personal_relevance_score"] if row else None
    cr.upsert_concept(
        conn,
        label,
        concept_type,
        first_seen=first_seen,
        last_seen=last_seen,
        lifecycle_state=lifecycle_state,
        user_relationship=rel,
        world_signal_score=world_signal_score,
        personal_relevance_score=prs,
        metadata=metadata,
    )
    return concept_id


def _cluster_interest_map(conn: sqlite3.Connection) -> dict[str, list[str]] | None:
    """cluster_id(str) -> interest_ids from the personal-graph table
    evidence_links (src_kind='evidence_cluster', relation='supports',
    dst_kind='interest'). None when the table is absent or empty."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_links'"
    ).fetchone()
    if not has:
        return None
    rows = conn.execute(
        "SELECT src_id, dst_id FROM evidence_links"
        " WHERE src_kind='evidence_cluster' AND relation='supports'"
        "   AND dst_kind='interest'"
    ).fetchall()
    if not rows:
        return None
    m: dict[str, list[str]] = {}
    for src_id, dst_id in rows:
        m.setdefault(str(src_id), []).append(dst_id)
    return m


def _link_relevance(
    conn: sqlite3.Connection,
    concept_id: str,
    interest_ids: list[str],
    cluster_id: Any,
    run_id: str,
) -> None:
    for interest_id in interest_ids:
        cr.link_concept_interest(
            conn,
            concept_id,
            interest_id,
            method="shared_cluster",
            provenance={"cluster_id": cluster_id, "policy": POLICY_VERSION},
        )
    row = _existing_state(conn, concept_id)
    if row is not None and row["user_relationship"] in ("unknown", "adjacent"):
        cr.set_user_relationship(
            conn,
            concept_id,
            "adjacent",
            reason=f"evidence cluster {cluster_id} supports an operator "
            f"interest (shared_cluster, {POLICY_VERSION})",
            method="shared_cluster",
            run_id=run_id,
        )


def _scan_entity_v2(registry_conn, node_id, obs, label, as_of, as_of_d,
                    run_id, summary) -> None:
    """burst-policy-v2 entity path: decayed candidate gate, Gamma-Poisson
    rate-change signal, persistence episodes in the EXISTING
    trend_episodes table. Ranking score is preserved from v1 (v2 ranking
    calibration is OPEN); lifecycle promotion depends only on the v2
    policy."""
    concept_id = cr.concept_identity_id("entity", label)
    row = _existing_state(registry_conn, concept_id)
    meta = {}
    if row is not None and row["metadata_json"]:
        try:
            loaded = json.loads(row["metadata_json"])
            if isinstance(loaded, dict):
                meta = loaded
        except (TypeError, ValueError):
            meta = {}
    dec = bp2.evaluate(obs, as_of_d, meta.get("v2_evals"))
    current = row["lifecycle_state"] if row else None
    stats = _stats_for(obs, as_of_d)  # ranking score + audit features
    ep = cr.active_episode(registry_conn, concept_id)
    if not dec["candidate"] and not dec["positive"]:
        # Persist the negative evaluation so a later scan can see the
        # consecutive-negative COOL transition even after support decays
        # below the candidate gate (otherwise cool is unreachable).
        if row is not None:
            evals = [e for e in meta.get("v2_evals", [])
                     if e.get("as_of") != as_of][-1:]
            evals.append(dec["eval"])
            if ep is not None and dec["cool"]:
                cr.close_trend_episode(registry_conn, ep["episode_id"],
                                       ended_at=as_of, state="cooled")
            if dec["cool"] and current in ("emerging", "active"):
                cr.set_lifecycle(
                    registry_conn, concept_id, "cooling",
                    reason=f"burst-policy-v2 episode cooled at {as_of}",
                    run_id=run_id)
                current = "cooling"
            _preserve_upsert(
                registry_conn,
                label,
                "entity",
                first_seen=dec["first_seen"],
                last_seen=stats["last_seen"],
                lifecycle_state=current,
                world_signal_score=row["world_signal_score"],
                metadata={
                    "discovery_method": "entity_burst_v2",
                    "policy": bp2.POLICY_VERSION,
                    "node_id": node_id,
                    "recent_count": dec["recent_count_30d"],
                    "baseline_count": dec["k_base"],
                    "channels": dec["channels"],
                    "source_types": stats["source_types"],
                    "smoothed_ratio": round(stats["smoothed_ratio"], 4),
                    "v2_support": dec["support"],
                    "v2_candidate": dec["candidate"],
                    "v2_lifetime": dec["lifetime"],
                    "v2_k_recent": dec["k_recent"],
                    "v2_posterior": dec["posterior"],
                    "v2_evals": evals,
                },
            )
        return
    score = _world_signal(stats, as_of_d)
    if dec["promote"]:
        target = "emerging"
    elif current == "emerging" and dec["cool"]:
        target = "cooling"
    elif current == "emerging" and not dec["continue_active"]:
        target = "cooling"
    else:
        target = current or "candidate"
    evals = [e for e in meta.get("v2_evals", [])
             if e.get("as_of") != as_of][-1:]
    evals.append(dec["eval"])
    _preserve_upsert(
        registry_conn,
        label,
        "entity",
        first_seen=dec["first_seen"],
        last_seen=stats["last_seen"],
        lifecycle_state=target,
        world_signal_score=score,
        metadata={
            "discovery_method": "entity_burst_v2",
            "policy": bp2.POLICY_VERSION,
            "node_id": node_id,
            "recent_count": dec["recent_count_30d"],
            "baseline_count": dec["k_base"],
            "channels": dec["channels"],
            "source_types": stats["source_types"],  # audit only
            "smoothed_ratio": round(stats["smoothed_ratio"], 4),
            "v2_support": dec["support"],
            "v2_candidate": dec["candidate"],
            "v2_lifetime": dec["lifetime"],
            "v2_k_recent": dec["k_recent"],
            "v2_posterior": dec["posterior"],
            "v2_evals": evals,
        },
    )
    if target != current:
        cr.set_lifecycle(
            registry_conn, concept_id, target,
            reason=f"burst-policy-v2: support={dec['support']} "
                   f"posterior={dec['posterior']} channels={dec['channels']}",
            run_id=run_id)
    if dec["positive"] and ep is None:
        cr.open_trend_episode(
            registry_conn, concept_id, started_at=as_of,
            baseline_rate=round(dec["k_base"] / (bp2.PARAMS[
                    "baseline_window_days"] / bp2.PARAMS["time_unit_days"]), 4),
            policy_version=bp2.POLICY_VERSION,
            evidence={"run_id": run_id, "as_of": as_of,
                      "k_recent": dec["k_recent"], "k_base": dec["k_base"],
                      "posterior": dec["posterior"]})
        ep = cr.active_episode(registry_conn, concept_id)
    if ep is not None:
        if dec["positive"] or dec["continue_active"]:
            prev_peak = ep["peak_at"]
            peak = as_of if (dec["promote"] or prev_peak is None or
                             (ep["acceleration"] or 0) <= dec["posterior"])                 else prev_peak
            cr.update_trend_episode(
                registry_conn, ep["episode_id"],
                recent_rate=round(dec["k_recent"] / (bp2.PARAMS[
                    "recent_window_days"] / bp2.PARAMS["time_unit_days"]), 4),
                acceleration=dec["posterior"],
                source_diversity=stats["source_types"],
                independent_source_count=dec["channels"],
                novelty_score=1.0 if (as_of_d - date.fromisoformat(
                    dec["first_seen"])).days <= POLICY[
                    "novelty_first_seen_days"] else 0.0,
                last_active_at=as_of,
                peak_at=peak,
                evidence={"run_id": run_id, "as_of": as_of,
                          "support": dec["support"],
                          "posterior": dec["posterior"],
                          "k_recent": dec["k_recent"],
                          "k_base": dec["k_base"],
                          "promote": dec["promote"]})
        elif dec["cool"]:
            cr.close_trend_episode(registry_conn, ep["episode_id"],
                                   ended_at=as_of, state="cooled")
    if target == "emerging":
        summary["emerging"] += 1
    elif target == "candidate":
        summary["candidates"] += 1
    elif target == "cooling":
        summary["cooling"] += 1
    cr.record_observation(
        registry_conn, concept_id,
        source_kind="internal_scan",
        source_id=node_id,
        observed_at=as_of,
        title=label,
        evidence_ref=node_id,
        run_id=run_id,
        metadata={
            "discovery_method": "entity_burst_v2",
            "policy": bp2.POLICY_VERSION,
            "support": dec["support"],
            "posterior": dec["posterior"],
            "channels": dec["channels"],
            "world_signal": round(score, 4),
        },
    )


def scan_internal(
    registry_conn: sqlite3.Connection,
    catalog_path: Any = None,
    as_of: str | None = None,
    run_id: str | None = None,
    policy_version: str | None = None,
) -> dict:
    """One internal discovery scan. Reads the EF catalog (read-only),
    writes concepts/observations/episodes into the registry connection.
    Everything is computed strictly from observations dated <= as_of.

    policy_version: "burst-policy-v1" (production default) or
    "burst-policy-v2" (shadow). Explicit selection only; unknown
    versions raise."""
    t0 = time.time()
    as_of = as_of or time.strftime("%Y-%m-%d", time.gmtime())
    as_of_d = date.fromisoformat(as_of)
    if run_id is None:
        digest = hashlib.sha256(
            f"{as_of}\x1f{catalog_path}".encode("utf-8")
        ).hexdigest()[:8]
        run_id = f"run_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{digest}"
    policy_version = policy_version or POLICY_VERSION
    if policy_version not in ("burst-policy-v1", "burst-policy-v2"):
        raise ValueError(f"unknown policy_version: {policy_version!r}")
    cr.record_discovery_run(
        registry_conn, run_id, "internal_scan", policy_version, as_of
    )

    summary = {
        "run_id": run_id,
        "as_of": as_of,
        "entities_scanned": 0,
        "candidates": 0,
        "emerging": 0,
        "cooling": 0,
        "dormant": 0,
        "cluster_candidates": 0,
        "observations_recorded": 0,
        "runtime_s": 0.0,
    }

    cat = _catalog_ro(catalog_path)
    try:
        entity_obs = _entity_observations(cat, as_of)
        interest_map = _cluster_interest_map(cat)
        video_clusters: dict[str, set[int]] = {}
        if interest_map:
            for vid, cid in cat.execute(
                "SELECT DISTINCT video_id, cluster_id FROM chunk_clusters"
            ):
                video_clusters.setdefault(str(vid), set()).add(cid)
    finally:
        cat.close()

    summary["entities_scanned"] = len(entity_obs)
    dormant_cutoff = (as_of_d - timedelta(days=2 * POLICY["recent_window_days"])).isoformat()

    # ---- ENTITY SIGNAL ------------------------------------------------
    managed: dict[str, str] = {}  # node_id -> concept_id
    for node_id, obs in entity_obs.items():
        label = obs[0]["label"]
        concept_id = cr.concept_identity_id("entity", label)
        managed[node_id] = concept_id
        if policy_version == "burst-policy-v2":
            _scan_entity_v2(
                registry_conn, node_id, obs, label, as_of, as_of_d,
                run_id, summary)
            continue
        stats = _stats_for(obs, as_of_d)
        if stats["recent_count"] < POLICY["candidate_min_recent"]:
            continue  # handled by decay below
        score = _world_signal(stats, as_of_d)
        promoted = _is_emerging(stats)
        row = _existing_state(registry_conn, concept_id)
        current = row["lifecycle_state"] if row else None
        if promoted:
            target = "emerging"
        elif current in ("emerging", "active") and (
            stats["recent_count"] == 0
            or stats["recent_count"] < stats["baseline_count"]
        ):
            target = "cooling"
        else:
            target = current or "candidate"
        _preserve_upsert(
            registry_conn,
            label,
            "entity",
            first_seen=stats["first_seen"],
            last_seen=stats["last_seen"],
            lifecycle_state=target,
            world_signal_score=score,
            metadata={
                "discovery_method": "entity_burst",
                "policy": POLICY_VERSION,
                "node_id": node_id,
                "recent_count": stats["recent_count"],
                "baseline_count": stats["baseline_count"],
                "channels": stats["channels"],
                "source_types": stats["source_types"],
                "smoothed_ratio": round(stats["smoothed_ratio"], 4),
            },
        )
        if target != current:
            cr.set_lifecycle(
                registry_conn,
                concept_id,
                target,
                reason=f"entity burst policy {POLICY_VERSION}: "
                f"recent={stats['recent_count']} baseline={stats['baseline_count']} "
                f"channels={stats['channels']} source_types={stats['source_types']}",
                run_id=run_id,
            )
        if target == "emerging":
            summary["emerging"] += 1
        elif target == "candidate":
            summary["candidates"] += 1
        elif target == "cooling":
            summary["cooling"] += 1
            ep = cr.active_episode(registry_conn, concept_id)
            if ep is not None:
                cr.close_trend_episode(
                    registry_conn, ep["episode_id"], ended_at=as_of, state="cooled"
                )
        cr.record_observation(
            registry_conn,
            concept_id,
            source_kind="internal_scan",
            source_id=node_id,
            observed_at=as_of,
            title=label,
            evidence_ref=node_id,
            run_id=run_id,
            metadata={
                "discovery_method": "entity_burst",
                "policy": POLICY_VERSION,
                "recent_count": stats["recent_count"],
                "baseline_count": stats["baseline_count"],
                "channels": stats["channels"],
                "source_types": stats["source_types"],
                "world_signal": round(score, 4),
            },
        )
        if target == "emerging":
            episode_id = cr.open_trend_episode(
                registry_conn,
                concept_id,
                started_at=min(
                    o["obs_date"]
                    for o in obs
                    if date.fromisoformat(o["obs_date"])
                    > as_of_d - timedelta(days=POLICY["recent_window_days"])
                ),
                baseline_rate=float(stats["baseline_count"]),
                policy_version=POLICY_VERSION,
                evidence={"run_id": run_id, "as_of": as_of},
            )
            cr.update_trend_episode(
                registry_conn,
                episode_id,
                recent_rate=float(stats["recent_count"]),
                acceleration=round(stats["smoothed_ratio"], 4),
                source_diversity=stats["source_types"],
                independent_source_count=stats["channels"],
                novelty_score=1.0
                if (as_of_d - date.fromisoformat(stats["first_seen"])).days
                <= POLICY["novelty_first_seen_days"]
                else 0.0,
                last_active_at=stats["last_seen"],
            )
        # personal relevance via shared clusters
        if interest_map:
            for cid in sorted(
                {c for v in stats["recent_video_ids"] for c in video_clusters.get(v, ())}
            ):
                ids = interest_map.get(str(cid))
                if ids:
                    _link_relevance(registry_conn, concept_id, ids, cid, run_id)

    # ---- DECAY (entity concepts managed here, with no current candidacy)
    for row in registry_conn.execute(
        "SELECT * FROM concepts WHERE concept_type = 'entity'"
    ).fetchall():
        meta = {}
        if row["metadata_json"]:
            try:
                loaded = json.loads(row["metadata_json"])
                if isinstance(loaded, dict):
                    meta = loaded
            except (TypeError, ValueError):
                meta = {}
        if meta.get("discovery_method") != "entity_burst":
            continue
        if meta.get("policy", POLICY_VERSION) != POLICY_VERSION:
            continue  # v2 concepts are managed by the v2 scan path
        concept_id = row["concept_id"]
        state = row["lifecycle_state"]
        if state in ("emerging", "active"):
            node_id = meta.get("node_id")
            obs = entity_obs.get(node_id, [])
            stats = _stats_for(obs, as_of_d) if obs else None
            recent = stats["recent_count"] if stats else 0
            baseline = stats["baseline_count"] if stats else 0
            if recent < POLICY["candidate_min_recent"] or recent < baseline:
                cr.set_lifecycle(
                    registry_conn,
                    concept_id,
                    "cooling",
                    reason=f"burst decayed below baseline ({POLICY_VERSION})",
                    run_id=run_id,
                )
                summary["cooling"] += 1
                ep = cr.active_episode(registry_conn, concept_id)
                if ep is not None:
                    cr.close_trend_episode(
                        registry_conn, ep["episode_id"], ended_at=as_of, state="cooled"
                    )
        elif state == "cooling":
            last = row["last_seen"]
            if last is not None and last < dormant_cutoff:
                cr.set_lifecycle(
                    registry_conn,
                    concept_id,
                    "dormant",
                    reason=f"no activity for >2x recent window ({POLICY_VERSION})",
                    run_id=run_id,
                )
                summary["dormant"] += 1

    # ---- CLUSTER SIGNAL ------------------------------------------------
    inv = evidence_cluster_inventory(catalog_path=catalog_path)
    cutoff_month = (as_of_d - timedelta(days=60)).strftime("%Y-%m")
    cluster_concepts: dict[str, Any] = {}
    for cl in inv["clusters"]:
        recent_first = cl["first_month"] is not None and cl["first_month"] >= cutoff_month
        if not (recent_first or (cl["phase"] == "emerging" and cl["channels"] >= 3)):
            continue
        label = cl["label"]
        cid = cl["cluster_id"]
        concept_id = cr.concept_identity_id("topic_cluster", label)
        cluster_concepts[str(cid)] = concept_id
        _preserve_upsert(
            registry_conn,
            label,
            "topic_cluster",
            first_seen=(cl["first_month"] or as_of) + "-01",
            last_seen=as_of,
            lifecycle_state="candidate",
            world_signal_score=None,
            metadata={
                "discovery_method": "semantic_cluster",
                "policy": POLICY_VERSION,
                "cluster_id": cid,
                "evidence_signature": cl["evidence_signature"],
                "channels": cl["channels"],
                "documents": cl["documents"],
                "first_month": cl["first_month"],
            },
        )
        summary["cluster_candidates"] += 1
        cr.record_observation(
            registry_conn,
            concept_id,
            source_kind="evidence_cluster",
            source_id=str(cid),
            observed_at=as_of,
            title=label,
            evidence_ref=f"cluster:{cid}",
            run_id=run_id,
            metadata={
                "discovery_method": "semantic_cluster",
                "policy": POLICY_VERSION,
                "cluster_id": cid,
                "evidence_signature": cl["evidence_signature"],
            },
        )
        if interest_map and interest_map.get(str(cid)):
            _link_relevance(
                registry_conn, concept_id, interest_map[str(cid)], cid, run_id
            )

    summary["observations_recorded"] = int(
        registry_conn.execute(
            "SELECT COUNT(*) FROM concept_observations WHERE discovery_run_id = ?",
            (run_id,),
        ).fetchone()[0]
    )
    summary["runtime_s"] = round(time.time() - t0, 3)
    cr.complete_discovery_run(registry_conn, run_id, "complete", summary)
    return summary
