"""Observed-layer interest statistics over the yt-is corpus.

v1 of the interest-graph design (docs/design/interest-graph-2026-08-24.md):
the MECHANICAL layer only — per-entity breadth/depth/persistence/recency
computed in SQL from the knowledge graph + entity tables. No LLM, no
stance/confidence claims: everything here is OBSERVED, and the page says so.
The v2 LLM interpretation layer consumes this module's output.

Dimensions (operator spec):
  breadth     distinct channels mentioning the entity (independent evidence)
  depth       total chunk hits (intensity proxy)
  persistence months with >=1 hit (durable vs spike)
  recency     last-seen month; emerging = recent + growing, dormant =
              old persistence + stale recency
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")

SOURCE_LABELS = {
    "notebooklm": "youtube", "ytdlp": "youtube", "selenium": "youtube",
    "whisper": "youtube", "hackernews": "hn",
}

# one broad-channel/incidental marker class for v1: entities whose hits
# come almost entirely from ONE channel are suspect as channel-specific
# vocabulary rather than user interest (negative-evidence heuristic #1;
# the LLM layer owns the full negative-evidence call)


def observed_entities(limit: int = 60) -> list[dict]:
    """Top entities by a breadth-weighted composite (spec: do not equate
    frequency with importance — depth alone would do exactly that)."""
    with sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=30) as c:
        c.execute("PRAGMA busy_timeout=30000")
        rows = c.execute(r"""
            SELECT en.node_id, en.label,
                   COUNT(DISTINCT ch.node_id)              AS breadth,
                   SUM(m.weight)                           AS depth,
                   COUNT(DISTINCT ch.node_id) * 1.0
                     / (1 + SUM(m.weight) / 500.0)         AS composite
            FROM kg_edges m
            JOIN kg_edges ic ON ic.src_id = m.dst_id
                 AND ic.relation = 'in_channel'
            JOIN kg_nodes ch ON ch.node_id = ic.dst_id
            JOIN kg_nodes en ON en.node_id = m.src_id
            WHERE m.relation = 'mentioned_in'
            GROUP BY en.node_id
            ORDER BY composite DESC
            LIMIT ?""", (limit,)).fetchall()
        out = []
        for node_id, label, breadth, depth, _comp in rows:
            # temporal: month spread over eu captured_at (YouTube) /
            # published_at where present; fallback to captured_at
            t = c.execute(r"""
                SELECT substr(MIN(COALESCE(NULLIF(eu.published_at,''),
                                        eu.captured_at)), 1, 7),
                       substr(MAX(COALESCE(NULLIF(eu.published_at,''),
                                        eu.captured_at)), 1, 7),
                       COUNT(DISTINCT substr(COALESCE(
                           NULLIF(eu.published_at,''), eu.captured_at), 1, 7))
                FROM kg_edges m
                JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
                WHERE m.src_id = ? AND m.relation = 'mentioned_in'""",
                (node_id,)).fetchone()
            first_m, last_m, months = t
            srcs = c.execute(r"""
                SELECT eu.source, COUNT(*) FROM kg_edges m
                JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
                WHERE m.src_id = ? AND m.relation = 'mentioned_in'
                GROUP BY 1 ORDER BY 2 DESC""", (node_id,)).fetchall()
            merged: dict[str, int] = {}
            for s, n in srcs:
                key = SOURCE_LABELS.get(s, s)
                merged[key] = merged.get(key, 0) + n
            emerging = None
            if last_m and months:
                # emerging heuristic: first seen recently yet already
                # multi-month persistent (not a one-month spike)
                recent_start = first_m >= "2026-06"
                emerging = "emerging" if recent_start and months >= 2 \
                    else ("dormant" if last_m < "2026-05" and months >= 3
                          else None)
            out.append({
                "node_id": node_id, "label": label,
                "breadth": breadth, "depth": int(depth or 0),
                "first_month": first_m, "last_month": last_m,
                "active_months": months or 0,
                "phase": emerging,
                "sources": sorted(merged.items(), key=lambda kv: -kv[1]),
            })
        return out


def corpus_summary() -> dict:
    with sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=30) as c:
        c.execute("PRAGMA busy_timeout=30000")
        return {
            "entities": c.execute(
                "SELECT COUNT(*) FROM kg_nodes WHERE kind='entity'"
            ).fetchone()[0],
            "channels": c.execute(
                "SELECT COUNT(*) FROM kg_nodes WHERE kind='channel'"
            ).fetchone()[0],
            "documents": c.execute(
                "SELECT COUNT(*) FROM kg_nodes WHERE kind='eu'"
            ).fetchone()[0],
        }
