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

Layering (2026-08-24 refactor): the mechanical cluster INVENTORY
(evidence_cluster_inventory) enumerates the COMPLETE eligible universe
with no top-N truncation; targeted full-packet HYDRATION
(hydrate_evidence_clusters) materializes packets for explicit cluster
id sets. Dashboard top-N stays allowed; inference bootstrap top-N is
not. The legacy evidence_clusters() entry point is preserved as
inventory -> rank -> hydrate for /interests compatibility.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
BATCH = Path("P:/.data/yt-is/batch_status.sqlite")
TRANSCRIPTS = Path("P:/.data/yt-is/transcripts.sqlite")

SOURCE_LABELS = {
    "notebooklm": "youtube", "ytdlp": "youtube", "selenium": "youtube",
    "whisper": "youtube", "hackernews": "hn",
}


def _catalog(catalog_path=None) -> sqlite3.Connection:
    path = CATALOG if catalog_path is None else Path(catalog_path)
    uri = path.as_posix()
    c = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    return c


def _cluster_stats_rows(c, candidate_sql: str, params: tuple):
    """Aggregate per-cluster stats (channels/documents/month window)
    with ONE GROUP BY over eu JOIN chunk_clusters restricted to the
    non-series candidate clusters — never one query per cluster."""
    return c.execute(rf"""
        SELECT cc.cluster_id,
               COUNT(DISTINCT eu.channel_id),
               COUNT(DISTINCT eu.eu_id),
               substr(MIN(COALESCE(NULLIF(eu.published_at,''),
                            eu.captured_at)), 1, 7),
               substr(MAX(COALESCE(NULLIF(eu.published_at,''),
                            eu.captured_at)), 1, 7),
               COUNT(DISTINCT substr(COALESCE(
                   NULLIF(eu.published_at,''), eu.captured_at), 1, 7))
        FROM eu
        JOIN chunk_clusters cc ON cc.video_id = eu.video_id
        WHERE cc.cluster_id IN ({candidate_sql})
        GROUP BY cc.cluster_id""", params).fetchall()


def _source_rows(c, candidate_sql: str, params: tuple):
    return c.execute(rf"""
        SELECT cc.cluster_id, eu.source, COUNT(DISTINCT eu.eu_id)
        FROM eu
        JOIN chunk_clusters cc ON cc.video_id = eu.video_id
        WHERE cc.cluster_id IN ({candidate_sql})
        GROUP BY cc.cluster_id, eu.source""", params).fetchall()


def evidence_cluster_inventory(min_member_count: int = 40,
                               min_channels: int = 3,
                               catalog_path=None) -> dict:
    """Enumerate the COMPLETE mechanically eligible cluster universe.

    Dashboard top-N stays allowed; inference bootstrap top-N is not —
    this function applies ZERO top-N / LIMIT. Eligibility is exactly:
    topic_clusters.is_series = 0 AND member_count >= min_member_count
    AND distinct-channel breadth >= min_channels. Nothing else.

    Entity specificity (kg_edges/kg_nodes) is deliberately NOT
    computed here: it is hydration-only work.
    """
    # "Dashboard top-N stays allowed; inference bootstrap top-N is not."
    with closing(_catalog(catalog_path)) as c:
        total_non_series, series_n = c.execute(
            "SELECT SUM(is_series = 0), SUM(is_series = 1)"
            " FROM topic_clusters").fetchone()
        total_non_series = int(total_non_series or 0)
        series_n = int(series_n or 0)

        below_floor = int(c.execute(
            "SELECT COUNT(*) FROM topic_clusters"
            " WHERE is_series = 0 AND member_count < ?",
            (min_member_count,)).fetchone()[0])

        rows = c.execute("""
            SELECT cluster_id, label, member_count, video_count, top_terms
            FROM topic_clusters
            WHERE is_series = 0 AND member_count >= ?
            ORDER BY cluster_id""", (min_member_count,)).fetchall()

        candidate_sql = ("SELECT cluster_id FROM topic_clusters"
                         " WHERE is_series = 0 AND member_count >= ?")
        stats = {r[0]: r[1:] for r in
                 _cluster_stats_rows(c, candidate_sql, (min_member_count,))}
        srcs: dict[int, dict[str, int]] = {}
        for cl, s, n in _source_rows(c, candidate_sql,
                                     (min_member_count,)):
            srcs.setdefault(cl, {})[s] = n

    clusters = []
    channels_below = 0
    for cluster_id, label, members, videos, terms in rows:
        chan, docs, first_m, last_m, months = stats.get(
            cluster_id, (0, 0, None, None, 0))
        if chan < min_channels:
            channels_below += 1
            continue
        merged: dict[str, int] = {}
        for s, n in srcs.get(cluster_id, {}).items():
            key = SOURCE_LABELS.get(s, s)
            merged[key] = merged.get(key, 0) + n
        sources = sorted(merged.items(),
                         key=lambda kv: (-kv[1], kv[0]))
        term_list = json.loads(terms) if terms else []
        entry_terms = term_list[:8]
        phase = None
        if first_m and months:
            if first_m >= "2026-06" and months >= 2:
                phase = "emerging"
            elif last_m < "2026-05" and months >= 3:
                phase = "dormant"
        sig = hashlib.sha256(json.dumps({
            "cluster_id": cluster_id, "member_count": members,
            "video_count": videos, "channels": chan, "documents": docs,
            "active_months": months, "first_month": first_m,
            "last_month": last_m, "terms": term_list[:10],
            "sources": sources,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        clusters.append({
            "cluster_id": cluster_id, "label": label,
            "member_count": members, "video_count": videos,
            "channels": int(chan), "documents": int(docs),
            "active_months": int(months), "first_month": first_m,
            "last_month": last_m, "phase": phase, "sources": sources,
            "terms": entry_terms, "evidence_signature": sig.hexdigest()[:16],
        })
    return {
        "clusters": clusters,
        "eligible_count": len(clusters),
        "total_semantic_non_series": total_non_series,
        "exclusions": {
            "series": series_n,
            "member_count_below_floor": below_floor,
            "channels_below_floor": channels_below,
        },
    }


def hydrate_evidence_clusters(cluster_ids, min_member_count: int = 40,
                              min_channels: int = 3,
                              catalog_path=None) -> list[dict]:
    """Materialize FULL v1.5 evidence packets for explicit cluster ids.

    Dashboard top-N stays allowed; inference bootstrap top-N is not —
    this function hydrates exactly the ids it is given (deduplicated,
    ascending order). Unknown or ineligible ids raise ValueError: they
    must never silently enter bootstrap inference.
    """
    ids = sorted({int(i) for i in cluster_ids})
    if not ids:
        return []
    with closing(_catalog(catalog_path)) as c:
        rows = c.execute(
            "SELECT cluster_id, label, member_count, video_count,"
            " top_terms, is_series FROM topic_clusters"
            " WHERE cluster_id IN (%s)" % ",".join("?" * len(ids)),
            ids).fetchall()
        by_id = {r[0]: r for r in rows}

        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise ValueError(f"unknown cluster ids: {sorted(unknown)}")

        ineligible = []
        for i in ids:
            _, _, members, _, _, is_series = by_id[i]
            if is_series:
                ineligible.append(
                    f"{i} (is_series=1)")
                continue
            if members < min_member_count:
                ineligible.append(
                    f"{i} (member_count {members} < {min_member_count})")
        stats = {r[0]: r[1:] for r in
                 _cluster_stats_rows(c, "SELECT cluster_id FROM"
                                        " topic_clusters WHERE"
                                        " is_series = 0 AND"
                                        " member_count >= ?",
                                     (min_member_count,))}
        for i in ids:
            if any(e.startswith(f"{i} ") for e in ineligible):
                continue
            st = stats.get(i)
            if st is None or st[0] < min_channels:
                chan = st[0] if st else 0
                ineligible.append(
                    f"{i} (channel breadth {chan} < {min_channels})")
        if ineligible:
            raise ValueError(f"ineligible cluster ids: {sorted(ineligible)}")

        # corpus-wide entity spread for specificity weighting:
        # entity -> (cluster_id, videos) across ALL clusters it touches.
        # Path: entity →(mentioned_in)→ eu node → eu.video_id → cluster.
        # Computed ONCE per call — never once per cluster.
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
        for i in ids:
            cluster_id, label, members, videos, terms, _ = by_id[i]
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
        out.sort(key=lambda d: d["cluster_id"])
        return out


def evidence_clusters(min_member_count: int = 40,
                      top_clusters: int = 40) -> list[dict]:
    """Build evidence packets for the strongest topic clusters.

    Ordering: cluster breadth (distinct channels) desc — the operator's
    independent-evidence principle applied at cluster level. Series
    clusters (is_series=1, one channel's playlist echo) are excluded:
    they are popularity artifacts, not interest evidence.

    Implementation note: the pre-refactor implementation pre-truncated
    the candidate pool to the top 2*top_clusters by member_count before
    eligibility filtering. Drawing the same top-N ranking from the
    complete eligible pool (via evidence_cluster_inventory) is a
    compatible improvement that strictly follows the breadth principle.
    """
    inv = evidence_cluster_inventory(min_member_count=min_member_count,
                                     min_channels=3)
    ranked = sorted(inv["clusters"],
                    key=lambda d: (-d["channels"], -d["documents"],
                                   d["cluster_id"]))
    chosen = [d["cluster_id"] for d in ranked[:top_clusters]]
    if not chosen:
        return []
    out = hydrate_evidence_clusters(chosen,
                                    min_member_count=min_member_count,
                                    min_channels=3)
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
    with closing(sqlite3.connect(f"file:{BATCH}?mode=ro", uri=True,
                               timeout=30)) as b:
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
    with closing(_catalog()) as c:
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
