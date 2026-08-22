#!/usr/bin/env python
"""wiki_traversal — recursive-CTE baseline for the graph bake-off (packet P2/C2).

Three acceptance tests (the wiki backlog's highest-leverage items):
  1. backlinks: reverse-link query — what cites this page?
  2. wiki↔handoff: cross-store link query — which handoffs cite this concept?
  3. evidence-chain: concept → receipts → corpus walk

Implementation: SQLite recursive CTEs over the relations frontmatter and
handoff body citations. This is the BASELINE arm of the bake-off —
Kùzu is disqualified (archived 2025-10, format never stabilized, see
[github.com/kuzudb/kuzu]) so the comparison is CTE vs LightRAG.

Budgets (red-team R-budgets): backlinks <2s, wiki↔handoff <3s, evidence <5s p95.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

VAULT = Path("P:/.data/wiki/concepts")
HANDOFFS = Path("P:/docs/handoffs")
DB = Path("P:/.data/scout/wiki-traversal.sqlite")
RELATION_RE = re.compile(r"target:\s*(?:\[\[)?([^\]\n]+?)(?:\]\])?\s*$")
WIKILINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")


def build_db():
    """Extract edges from wiki frontmatter + handoff citations into SQLite."""
    conn = sqlite3.connect(str(DB))
    conn.executescript("""
        DROP TABLE IF EXISTS wiki_edges;
        DROP TABLE IF EXISTS handoff_cites;
        CREATE TABLE wiki_edges (source TEXT, target TEXT, edge_type TEXT);
        CREATE TABLE handoff_cites (handoff TEXT, cited_path TEXT);
        CREATE INDEX idx_we_source ON wiki_edges(source);
        CREATE INDEX idx_we_target ON wiki_edges(target);
        CREATE INDEX idx_hc_cited ON handoff_cites(cited_path);
    """)

    for p in sorted(VAULT.glob("*.md")):
        slug = p.stem
        text = p.read_text(encoding="utf-8", errors="replace")
        # frontmatter relations
        if text.startswith("---"):
            fm_end = text.find("\n---", 3)
            if fm_end > 0:
                fm = text[3:fm_end]
                for line in fm.splitlines():
                    m = RELATION_RE.match(line.strip())
                    if m:
                        target = m.group(1).strip().rstrip("/")
                        target_slug = target.split("/")[-1]
                        conn.execute("INSERT INTO wiki_edges VALUES (?,?,?)",
                                     (slug, target_slug, "relation"))
        # body wikilinks
        for m in WIKILINK_RE.finditer(text):
            conn.execute("INSERT INTO wiki_edges VALUES (?,?,?)",
                         (slug, m.group(1).strip(), "wikilink"))

    for h in sorted(HANDOFFS.rglob("HANDOFF.md")):
        htext = h.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"(?:`|\[\[)([\w/.-]*(?:\.md|/concepts/\w+))", htext):
            cited = m.group(1)
            if "concepts/" in cited or cited.endswith(".md"):
                conn.execute("INSERT INTO handoff_cites VALUES (?,?)",
                             (str(h.relative_to(HANDOFFS)), cited))

    conn.commit()
    edge_count = conn.execute("SELECT COUNT(*) FROM wiki_edges").fetchone()[0]
    cite_count = conn.execute("SELECT COUNT(*) FROM handoff_cites").fetchone()[0]
    conn.close()
    return edge_count, cite_count


def backlinks(slug: str) -> list[tuple]:
    """Test 1: what cites this page? (reverse-link query, depth 1)"""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return conn.execute("""
            WITH RECURSIVE backlinks(slug, depth) AS (
                SELECT ?, 0
                UNION
                SELECT we.source, b.depth + 1
                FROM wiki_edges we JOIN backlinks b ON we.target = b.slug
                WHERE b.depth < 2
            )
            SELECT DISTINCT slug, depth FROM backlinks WHERE slug != ? ORDER BY depth, slug
        """, (slug, slug)).fetchall()
    finally:
        conn.close()


def handoff_links(fragment: str) -> list[tuple]:
    """Test 2: which handoffs cite concepts matching this fragment?"""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return conn.execute("""
            SELECT handoff, cited_path FROM handoff_cites
            WHERE cited_path LIKE ? LIMIT 50
        """, (f"%{fragment}%",)).fetchall()
    finally:
        conn.close()


def evidence_chain(slug: str, max_depth: int = 3) -> list[tuple]:
    """Test 3: walk from a concept outward through relation edges."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return conn.execute("""
            WITH RECURSIVE chain(slug, depth, path) AS (
                SELECT ?, 0, ?
                UNION
                SELECT we.target, c.depth + 1, c.path || ' -> ' || we.target
                FROM wiki_edges we JOIN chain c ON we.source = c.slug
                WHERE c.depth < ?
                  AND instr(c.path || ' -> ', we.target || ' -> ') = 0
            )
            SELECT slug, depth, path FROM chain WHERE depth > 0 ORDER BY depth, slug LIMIT 100
        """, (slug, slug, max_depth)).fetchall()
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "backlinks", "handoffs", "chain", "bench"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()

    if a.cmd == "build":
        edges, cites = build_db()
        print(f"built: {edges} wiki edges, {cites} handoff citations -> {DB}")
    elif a.cmd == "backlinks":
        rows = backlinks(a.arg)
        print(f"backlinks for {a.arg}: {len(rows)} results")
        for slug, d in rows[:15]:
            print(f"  [{d}] {slug}")
    elif a.cmd == "handoffs":
        rows = handoff_links(a.arg or "")
        print(f"handoff citations matching '{a.arg}': {len(rows)} results")
        for h, c in rows[:15]:
            print(f"  {h} -> {c}")
    elif a.cmd == "chain":
        rows = evidence_chain(a.arg)
        print(f"evidence chain from {a.arg}: {len(rows)} nodes")
        for slug, d, path in rows[:10]:
            print(f"  [{d}] {path[:100]}")
    elif a.cmd == "bench":
        build_db()
        t0 = time.perf_counter()
        backlinks("evidence-fabric")
        t1 = time.perf_counter()
        handoff_links("evidence-fabric")
        t2 = time.perf_counter()
        evidence_chain("two-clock-model-ingestion-vs-evidence-time")
        t3 = time.perf_counter()
        print(f"backlinks:  {(t1-t0)*1000:.0f}ms (budget 2000ms)")
        print(f"handoffs:   {(t2-t1)*1000:.0f}ms (budget 3000ms)")
        print(f"chain:      {(t3-t2)*1000:.0f}ms (budget 5000ms)")
        verdict = "PASS" if (t1-t0) < 2 and (t2-t1) < 3 and (t3-t2) < 5 else "FAIL"
        print(f"verdict: {verdict}")
