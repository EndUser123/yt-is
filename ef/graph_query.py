"""Neighborhood queries over the knowledge graph (kg_nodes/kg_edges).

The data layer (scripts/build_knowledge_graph.py) stores eu nodes as
'eu:<eu_id>' — every join back to the eu table strips that prefix.
This module is read-only against catalog.sqlite and backs /graph on
the warm query service: entity/channel lookup, top documents, channel
and source breakdowns, and co-mentioned entities.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")

# pipeline-internal source tags -> display names
SOURCE_LABELS = {
    "notebooklm": "youtube",      # the NLM drain IS the YouTube pipeline
    "ytdlp": "youtube",
    "selenium": "youtube",
    "whisper": "youtube",
    "hackernews": "hn",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def search_nodes(q: str, limit: int = 15) -> list[dict]:
    """Entity/channel matches for the search box (prefix then infix)."""
    q = (q or "").strip()
    if not q:
        return []
    with closing(_connect()) as c:
        rows = c.execute(
            """SELECT node_id, kind, label, weight FROM kg_nodes
               WHERE kind IN ('entity', 'channel')
                 AND (label LIKE ? OR label LIKE ?)
               ORDER BY CASE WHEN label LIKE ? THEN 0 ELSE 1 END,
                        weight DESC LIMIT ?""",
            (f"{q}%", f"%{q}%", f"{q}%", limit)).fetchall()
    out = []
    for node_id, kind, label, weight in rows:
        out.append({"node_id": node_id, "kind": kind, "label": label,
                    "weight": weight,
                    "display": ("#" + label) if kind == "channel"
                    else label})
    return out


def entity_view(node_id: str, doc_limit: int = 12) -> dict:
    with closing(_connect()) as c:
        node = c.execute(
            "SELECT node_id, kind, label, weight FROM kg_nodes "
            "WHERE node_id = ?", (node_id,)).fetchone()
        if not node:
            return {"error": "node not found"}
        _, kind, label, weight = node

        docs = c.execute(
            """SELECT eu.title, eu.source, eu.channel_title, m.weight,
                      eu.eu_id
               FROM kg_edges m
               JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
               WHERE m.src_id = ? AND m.relation = 'mentioned_in'
               ORDER BY m.weight DESC LIMIT ?""",
            (node_id, doc_limit)).fetchall()

        sources = c.execute(
            """SELECT eu.source, COUNT(*) docs, SUM(m.weight) hits
               FROM kg_edges m
               JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
               WHERE m.src_id = ? AND m.relation = 'mentioned_in'
               GROUP BY 1 ORDER BY 2 DESC""",
            (node_id,)).fetchall()

        channels = c.execute(
            """SELECT ch.label, COUNT(*) docs, SUM(m.weight) hits
               FROM kg_edges m
               JOIN kg_edges ic ON ic.src_id = m.dst_id
                    AND ic.relation = 'in_channel'
               JOIN kg_nodes ch ON ch.node_id = ic.dst_id
               WHERE m.src_id = ? AND m.relation = 'mentioned_in'
               GROUP BY ch.node_id ORDER BY docs DESC LIMIT 10""",
            (node_id,)).fetchall()

        related = c.execute(
            """SELECT other.node_id, other.label,
                      COUNT(DISTINCT m1.dst_id) shared_docs
               FROM kg_edges m1
               JOIN kg_edges m2 ON m1.dst_id = m2.dst_id
                    AND m2.relation = 'mentioned_in'
                    AND m2.src_id != m1.src_id
               JOIN kg_nodes other ON other.node_id = m2.src_id
               WHERE m1.src_id = ?
               GROUP BY other.node_id ORDER BY shared_docs DESC LIMIT 12""",
            (node_id,)).fetchall()

        # lift-ranked co-mentions: raw shared_docs mostly recovers global
        # popularity (7 of AI's top-12 raw co-mentions ARE the corpus
        # top-12, measured 2026-08-24); lift = shared relative to both
        # entities' document counts surfaces DISTINCTIVE associates.
        # Floor of 5 shared docs keeps small-denominator noise out.
        lift_rows = c.execute(
            """SELECT other.node_id, other.label,
                      COUNT(DISTINCT m1.dst_id) shared_docs,
                      other.weight other_docs
               FROM kg_edges m1
               JOIN kg_edges m2 ON m1.dst_id = m2.dst_id
                    AND m2.relation = 'mentioned_in'
                    AND m2.src_id != m1.src_id
               JOIN kg_nodes other ON other.node_id = m2.src_id
               WHERE m1.src_id = ?
               GROUP BY other.node_id
               HAVING shared_docs >= 5
               ORDER BY (shared_docs * 1.0)
                        / (other.weight * ? + 1) DESC LIMIT 12""",
            (node_id, max(weight, 1))).fetchall()

    total_docs = sum(r[1] for r in sources) or len(docs)
    # several pipeline tags map to one display name (notebooklm/ytdlp ->
    # youtube): merge after labeling
    merged: dict[str, dict] = {}
    for s, n, h in sources:
        key = SOURCE_LABELS.get(s, s)
        m = merged.setdefault(key, {"source": key, "docs": 0, "hits": 0})
        m["docs"] += n
        m["hits"] += h or 0
    return {
        "node_id": node_id, "kind": kind, "label": label,
        "weight": weight, "total_docs": total_docs,
        "docs": [{"title": t or "(untitled)",
                  "source": SOURCE_LABELS.get(s, s), "channel": ch,
                  "hits": w}
                 for t, s, ch, w, _ in docs],
        "sources": sorted(merged.values(), key=lambda x: -x["docs"]),
        "channels": [{"channel": l, "docs": n, "hits": h}
                     for l, n, h in channels],
        "related": [{"node_id": nid, "label": l, "shared_docs": d}
                    for nid, l, d in related],
        "distinctive": [{"node_id": nid, "label": l, "shared_docs": d}
                        for nid, l, d, _w in lift_rows],
    }


def channel_view(node_id: str, ent_limit: int = 15) -> dict:
    with closing(_connect()) as c:
        node = c.execute(
            "SELECT node_id, kind, label, weight FROM kg_nodes "
            "WHERE node_id = ?", (node_id,)).fetchone()
        if not node:
            return {"error": "node not found"}
        _, kind, label, weight = node

        ents = c.execute(
            """SELECT en.node_id, en.label, SUM(m.weight) hits,
                      COUNT(DISTINCT m.dst_id) docs
               FROM kg_edges m
               JOIN kg_edges ic ON ic.src_id = m.dst_id
                    AND ic.relation = 'in_channel'
               JOIN kg_nodes en ON en.node_id = m.src_id
               WHERE ic.dst_id = ? AND m.relation = 'mentioned_in'
               GROUP BY en.node_id ORDER BY hits DESC LIMIT ?""",
            (node_id, ent_limit)).fetchall()

        meta = c.execute(
            """SELECT eu.source, COUNT(DISTINCT eu.eu_id) FROM eu
               JOIN kg_edges ic ON ic.src_id = 'eu:' || eu.eu_id
                    AND ic.relation = 'in_channel'
               WHERE ic.dst_id = ? GROUP BY 1""",
            (node_id,)).fetchall()

    return {
        "node_id": node_id, "kind": kind, "label": label,
        "weight": weight, "total_docs": weight,
        "entities": [{"node_id": nid, "label": l, "hits": h, "docs": d}
                     for nid, l, h, d in ents],
        "sources": [{"source": SOURCE_LABELS.get(s, s), "docs": n}
                    for s, n in meta],
    }
