"""Entity extraction over topic clusters — the knowledge-graph foundation.

For each topic cluster, pull its representative chunks (the proven PK-reopen
path from wiki_from_cluster), ask the LLM provider chain (codex -> agy ->
openrouter) to extract named entities with types, and store them in the
catalog. Then count each entity's corpus-wide footprint via indexed FTS
matches, giving cross-source connection data ("PyTorch: 1,204 chunks").

Why cluster-level LLM extraction instead of NER over all 285K chunks:
bounded cost (351 calls via free CLI providers), canonical names, and
entities are browsed by topic anyway. spaCy is broken on Python 3.14
(pydantic v1 conflict), and full-corpus LLM passes are unbounded.

Usage:
    python scripts/extract_entities.py               # all clusters
    python scripts/extract_entities.py --limit 20    # smoke run
    python scripts/extract_entities.py --counts-only # refresh FTS counts
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
FTS = Path("P:/.data/yt-is/ef/fts5.sqlite")
TDB = Path("P:/.data/yt-is/transcripts.sqlite")
CHUNKS_PER_CLUSTER = 6
MIN_ENTITY_LEN = 2
PAUSE_S = 2.0

PROMPT = """You extract named entities from text excerpts about the topic
"{topic}". Return ONLY a JSON array, no prose: one object per distinct
entity: {{"name": "...", "type": "PERSON|ORG|PRODUCT|TECH|CONCEPT|PLACE",
"mentions": <approx count in the excerpts>}}. Use the canonical spelling
(PyTorch not pytorch). Include tools, models, companies, people, protocols,
and key concepts. Exclude the topic name itself and generic words.

EXCERPTS:
{excerpts}"""


def _ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            entity TEXT NOT NULL,
            label TEXT NOT NULL,
            cluster_id INTEGER NOT NULL,
            mentions INTEGER DEFAULT 1,
            extracted_at TEXT,
            PRIMARY KEY (entity, cluster_id)
        );
        CREATE TABLE IF NOT EXISTS entity_corpus (
            entity TEXT PRIMARY KEY,
            label TEXT,
            chunk_count INTEGER,
            updated_at TEXT
        );
    """)
    conn.commit()


def cluster_chunks(conn, cluster_id: int, limit: int = CHUNKS_PER_CLUSTER):
    """Representative chunk text via PK lookups (chunk -> eu -> authority)."""
    rows = conn.execute(
        "SELECT video_id FROM chunk_clusters WHERE cluster_id = ? "
        "LIMIT 50", (cluster_id,)).fetchall()
    if not rows:
        return ""
    tdb = sqlite3.connect(f"file:{TDB}?mode=ro", uri=True, timeout=10.0)
    parts = []
    for (vid,) in rows[:limit]:
        span = conn.execute(
            """SELECT c.start_char, c.end_char, eu.authority_ref
               FROM chunk c JOIN eu ON eu.eu_id = c.eu_id
               WHERE eu.video_id = ? LIMIT 1""", (vid,)).fetchone()
        if not span:
            continue
        s, e, ref = span
        tr = tdb.execute(
            "SELECT substr(transcript, ?, ?) FROM transcript_cache "
            "WHERE cache_key = ?", (s + 1, e - s, ref)).fetchone()
        if tr and tr[0]:
            parts.append(tr[0])
    tdb.close()
    return "\n---\n".join(parts)[:12000]


def ask_llm(question: str) -> str | None:
    from ef import qa
    for _name, fn in qa._provider_chain():
        try:
            out = fn(question, "")  # context empty; prompt is the question
            if out:
                return out
        except Exception:
            continue
    return None


def extract_cluster(conn, cluster_id: int, label: str) -> int:
    text = cluster_chunks(conn, cluster_id)
    if len(text) < 200:
        return 0
    prompt = PROMPT.format(topic=label, excerpts=text)
    out = ask_llm(prompt)
    if not out:
        return 0
    m = re.search(r"\[.*\]", out, re.DOTALL)
    if not m:
        return 0
    try:
        entities = json.loads(m.group(0))
    except json.JSONDecodeError:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for e in entities:
        name = (e.get("name") or "").strip()
        etype = (e.get("label") or e.get("type") or "CONCEPT").strip().upper()
        mentions = int(e.get("mentions") or 1)
        if len(name) < MIN_ENTITY_LEN or etype not in (
                "PERSON", "ORG", "PRODUCT", "TECH", "CONCEPT", "PLACE"):
            continue
        conn.execute(
            """INSERT OR REPLACE INTO entities
                 (entity, label, cluster_id, mentions, extracted_at)
               VALUES (?, ?, ?, ?, ?)""",
            (name, etype, cluster_id, mentions, now))
        n += 1
    conn.commit()
    return n


def refresh_counts(conn):
    """Corpus-wide chunk counts per entity via indexed FTS matches."""
    fts = sqlite3.connect(f"file:{FTS}?mode=ro", uri=True, timeout=10.0)
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """SELECT entity, label, SUM(mentions) FROM entities
           GROUP BY entity, label
           HAVING SUM(mentions) >= 2
           ORDER BY SUM(mentions) DESC LIMIT 600""").fetchall()
    seen = set()
    deduped = []
    for entity, label, weight in rows:
        if entity in seen:
            continue
        seen.add(entity)
        deduped.append((entity, label, weight))
    rows = deduped[:400]
    updated = 0
    for entity, label, _weight in rows:
        try:
            n = fts.execute(
                "SELECT count(*) FROM chunks WHERE chunks MATCH ?",
                (f'"{entity}"',)).fetchone()[0]
        except Exception:
            continue
        conn.execute(
            """INSERT INTO entity_corpus (entity, label, chunk_count, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(entity) DO UPDATE SET
                 chunk_count = excluded.chunk_count,
                 updated_at = excluded.updated_at""",
            (entity, label, n, now))
        updated += 1
    conn.commit()
    fts.close()
    return updated


def main(argv=None):
    load_workspace_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--counts-only", action="store_true")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(str(CATALOG), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_tables(conn)

    if not args.counts_only:
        clusters = conn.execute(
            """SELECT tc.cluster_id, tc.label FROM topic_clusters tc
               WHERE tc.cluster_id != -1
               ORDER BY tc.member_count DESC
               LIMIT ?""",
            (args.limit or 10**9,)).fetchall()
        done = 0
        for cid, label in clusters:
            already = conn.execute(
                "SELECT 1 FROM entities WHERE cluster_id = ? LIMIT 1",
                (cid,)).fetchone()
            if already:
                continue
            n = extract_cluster(conn, cid, label)
            done += 1
            print(f"  cluster {cid} ({label[:30]}): {n} entities", flush=True)
            time.sleep(PAUSE_S)
        print(f"extracted from {done} clusters")

    n_counts = refresh_counts(conn)
    total = conn.execute("SELECT COUNT(*) FROM entity_corpus").fetchone()[0]
    conn.close()
    print(f"corpus counts refreshed for {n_counts} entities "
          f"({total} total tracked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
