"""Multi-view evidence clusters — the v1.5 layer of the interest graph.

Operator-directed revision (2026-08-24 external review): entities are
FEATURES supporting a cluster, not the ontology. The inference unit is
the evidence cluster: a semantic topic cluster fused with its
high-information entities, representative documents, cluster-level
temporal statistics, and source diversity. The v2 LLM consumes these
packets — never a cleaned entity leaderboard.

Views fused per cluster (operator architecture):
  1. semantic topic cluster (topic_clusters, UMAP+HDBSCAN)
  2. high-information entities (specificity-weighted, not raw counts)
  3. representative documents (titles across times and sources)
  4. cluster temporal statistics (breadth/depth/persistence/recency
     at the CLUSTER level, not per entity)
  5. source diversity

Entity informativeness (review directive): downweight entities whose
mass spreads evenly across unrelated clusters — specificity is measured
as the share of an entity's cluster-document mass concentrated in the
candidate cluster vs its corpus spread. "recovery" alone is noise;
"recovery inside the HRV/overtraining/sleep cluster" is a feature.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
BATCH = Path("P:/.data/yt-is/batch_status.sqlite")
TRANSCRIPTS = Path("P:/.data/yt-is/transcripts.sqlite")

SOURCE_LABELS = {
    "notebooklm": "youtube", "ytdlp": "youtube", "selenium": "youtube",
    "whisper": "youtube", "hackernews": "hn",
}


def _catalog() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    return c


def evidence_clusters(min_member_count: int = 40,
                      top_clusters: int = 40) -> list[dict]:
    """Build evidence packets for the strongest topic clusters.

    Ordering: cluster breadth (distinct channels) desc — the operator's
    independent-evidence principle applied at cluster level. Series
    clusters (is_series=1, one channel's playlist echo) are excluded:
    they are popularity artifacts, not interest evidence.
    """
    with _catalog() as c:
        clusters = c.execute("""
            SELECT cluster_id, label, member_count, video_count, top_terms
            FROM topic_clusters
            WHERE is_series = 0 AND member_count >= ?
            ORDER BY member_count DESC LIMIT ?""",
            (min_member_count, top_clusters * 2)).fetchall()

        # corpus-wide entity spread for specificity weighting:
        # entity -> (cluster_id, videos) across ALL clusters it touches.
        # Path: entity →(mentioned_in)→ eu node → eu.video_id → cluster.
        spread: dict[str, dict[int, int]] = {}
        for ent, cl, n in c.execute(r"""
                SELECT en.label, cc.cluster_id, COUNT(DISTINCT eu.video_id)
                FROM kg_edges m
                JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
                JOIN chunk_clusters cc ON cc.video_id = eu.video_id
                JOIN kg_nodes en ON en.node_id = m.src_id
                WHERE m.relation = 'mentioned_in'
                GROUP BY en.label, cc.cluster_id"""):
            spread.setdefault(ent, {})[cl] = n

        out = []
        for cluster_id, label, members, videos, terms in clusters:
            # temporal + channel stats at cluster level
            t = c.execute(r"""
                SELECT COUNT(DISTINCT eu.channel_id),
                       COUNT(DISTINCT eu.eu_id),
                       substr(MIN(COALESCE(NULLIF(eu.published_at,''),
                                    eu.captured_at)), 1, 7),
                       substr(MAX(COALESCE(NULLIF(eu.published_at,''),
                                    eu.captured_at)), 1, 7),
                       COUNT(DISTINCT substr(COALESCE(
                           NULLIF(eu.published_at,''), eu.captured_at), 1, 7))
                FROM eu
                JOIN chunk_clusters cc ON cc.video_id = eu.video_id
                WHERE cc.cluster_id = ?""", (cluster_id,)).fetchone()
            chan_breadth, docs, first_m, last_m, months = t
            if chan_breadth < 3:
                continue  # single-channel clusters are not interests

            srcs = dict(c.execute(r"""
                SELECT eu.source, COUNT(DISTINCT eu.eu_id) FROM eu
                JOIN chunk_clusters cc ON cc.video_id = eu.video_id
                WHERE cc.cluster_id = ? GROUP BY 1""",
                (cluster_id,)).fetchall())
            merged: dict[str, int] = {}
            for s, n in srcs.items():
                key = SOURCE_LABELS.get(s, s)
                merged[key] = merged.get(key, 0) + n

            # high-information entities: specificity = share of the
            # entity's cluster mass held by THIS cluster
            ents = c.execute(r"""
                    SELECT en.label, COUNT(DISTINCT eu.video_id) dv
                    FROM kg_edges m
                    JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
                    JOIN chunk_clusters cc ON cc.video_id = eu.video_id
                    JOIN kg_nodes en ON en.node_id = m.src_id
                    WHERE m.relation = 'mentioned_in' AND cc.cluster_id = ?
                    GROUP BY en.label ORDER BY dv DESC LIMIT 40""",
                (cluster_id,)).fetchall()
            informative = []
            for label_e, dv in ents:
                dist = spread.get(label_e, {})
                total = sum(dist.values()) or 1
                specificity = dist.get(cluster_id, 0) / total
                # calibration (2026-08-24, cluster 0 diagnosis): even
                # genuinely cluster-specific entities cap at ~0.13 share
                # at this cluster granularity (319 coarse clusters) — a
                # 0.25 threshold filtered EVERYTHING. Keep entities that
                # are either measurably concentrated (>=0.08 with real
                # mass) or dominant in absolute terms (>=30 videos).
                keep = (specificity >= 0.08 and dv >= 3) or dv >= 30
                if not keep:
                    continue
                informative.append({
                    "entity": label_e, "videos": dv,
                    "specificity": round(specificity, 2)})
                if len(informative) >= 12:
                    break

            # representative documents: distinct sources, spread over time
            reps = c.execute(r"""
                    SELECT DISTINCT eu.title, eu.channel_title, eu.source,
                           substr(COALESCE(NULLIF(eu.published_at,''),
                                           eu.captured_at), 1, 7) mon
                    FROM eu
                    JOIN chunk_clusters cc ON cc.video_id = eu.video_id
                    WHERE cc.cluster_id = ? AND eu.title IS NOT NULL
                      AND length(eu.title) > 12
                    ORDER BY mon DESC LIMIT 60""", (cluster_id,)).fetchall()
            picked, seen_titles = [], set()
            for title, ch_t, src, mon in reps:
                key = (title or "")[:40]
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                picked.append({"title": (title or "")[:90],
                               "channel": (ch_t or "")[:40],
                               "source": SOURCE_LABELS.get(src, src),
                               "month": mon})
                if len(picked) >= 8:
                    break

            phase = None
            if first_m and months:
                if first_m >= "2026-06" and months >= 2:
                    phase = "emerging"
                elif last_m < "2026-05" and months >= 3:
                    phase = "dormant"

            out.append({
                "cluster_id": cluster_id, "label": label,
                "terms": json.loads(terms) if terms else [],
                "channels": chan_breadth, "documents": docs,
                "videos": videos, "active_months": months,
                "first_month": first_m, "last_month": last_m,
                "phase": phase,
                "sources": sorted(merged.items(), key=lambda kv: -kv[1]),
                "entities": informative, "representative": picked,
            })
            if len(out) >= top_clusters:
                break
        out.sort(key=lambda d: (-d["channels"], -d["documents"]))
        return out


CACHE = Path("P:/.data/yt-is/ef/evidence-clusters.json")
CACHE_TTL_S = 6 * 3600


def cached_clusters(max_age_s: int = CACHE_TTL_S,
                    refresh: bool = False) -> tuple[list[dict], dict]:
    """Page-facing entry: build is ~45s (kg-wide spread query); serve
    from the JSON cache unless stale or ?refresh=1."""
    import time as _t
    if not refresh and CACHE.exists():
        try:
            blob = json.loads(CACHE.read_text(encoding="utf-8"))
            if _t.time() - blob.get("built_at_epoch", 0) < max_age_s:
                return blob["clusters"], blob["coverage"]
        except (OSError, ValueError, KeyError):
            pass
    clusters = evidence_clusters()
    coverage = coverage_chain()
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({
            "built_at": _t.strftime("%Y-%m-%dT%H:%M:%S"),
            "built_at_epoch": _t.time(),
            "clusters": clusters, "coverage": coverage},
            ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return clusters, coverage


def coverage_chain() -> dict:
    """Corpus universe → acquisition → indexed → evidence → clusters.
    The review's prerequisite: an absent interest may be missing data,
    not absent interest. Every /interests render carries this chain."""
    with sqlite3.connect(f"file:{BATCH}?mode=ro", uri=True, timeout=30) as b:
        b.execute("PRAGMA busy_timeout=30000")
        tracked = b.execute(
            "SELECT COUNT(*) FROM channel_metadata").fetchone()[0]
        blocked = b.execute(
            "SELECT COUNT(*) FROM channel_blocklist").fetchone()[0]
        cataloged = b.execute(
            "SELECT COUNT(*) FROM video_catalog").fetchone()[0]
        complete = b.execute(
            "SELECT COUNT(*) FROM analysis_status WHERE status='complete'"
        ).fetchone()[0]
    with _catalog() as c:
        indexed = c.execute("SELECT COUNT(*) FROM eu").fetchone()[0]
        clusters = c.execute(
            "SELECT COUNT(*) FROM topic_clusters WHERE is_series=0"
        ).fetchone()[0]
    return {
        "tracked_channels": tracked,
        "blocked_channels": blocked,
        "scanned_channels": tracked - min(blocked, tracked),
        "videos_cataloged": cataloged,
        "videos_transcript_complete": complete,
        "documents_indexed_ef": indexed,
        "semantic_clusters": clusters,
    }
