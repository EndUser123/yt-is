"""Promote staged yt-is cluster pages into the workspace wiki vault.

Implements the Phase 1 roadmap item "wiki page promotion": takes the
cluster concept pages staged by wiki_from_cluster.py and rewrites them
with vault-schema-compliant frontmatter (P:/.data/wiki/SCHEMA.md §2)
into P:/.data/wiki/concepts/, then appends to the vault log.

Collision policy (wiki-yt convention): never overwrite an existing
concept — a name collision is skipped with a notice.

Usage:
    python scripts/promote_wiki_pages.py            # promote all staged
    python scripts/promote_wiki_pages.py --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STAGING_DIR = Path("P:/.data/yt-is/visual/wiki-staging")
VAULT_CONCEPTS = Path("P:/.data/wiki/concepts")
VAULT_LOG = Path("P:/.data/wiki/log.md")


def parse_staged_page(path: Path) -> tuple[dict, str]:
    """Split a staged page into frontmatter fields and body. Handles
    folded block scalars (summary: >) whose content lives on the
    following indented lines."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    fm_text, body = m.group(1), m.group(2)
    fields: dict[str, str] = {}
    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        mm = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if mm and not line.startswith((" ", "\t")):
            key, val = mm.group(1), mm.group(2).strip()
            if val in (">", "|", ">-", "|-"):
                block: list[str] = []
                j = i + 1
                while j < len(lines) and (lines[j].startswith((" ", "\t"))
                                          or lines[j] == ""):
                    if lines[j].strip():
                        block.append(lines[j].strip())
                    j += 1
                fields[key] = " ".join(block)
                i = j
                continue
            fields[key] = val
        i += 1
    return fields, body


def vault_frontmatter(f: dict, created: str) -> str:
    """Rewrite staged frontmatter per SCHEMA.md §2 (required + decay)."""
    title = f.get("title", "untitled").strip('"')
    summary = f.get("summary", "").strip(">")
    tags = f.get("tags", "[]").rstrip()
    return f"""---
title: "{title}"
created: {created}
source: {f.get('source', 'yt-is-cluster')}
tags: {tags}
type: concept
agent: zcode
host: both
cognitive_load: 2
verification: inferred-only
tier: warm
confidence: 0.8
last_verified: {created}
half_life_days: 180
summary: >
  {summary}
provenance: {f.get('provenance', 'topic cluster over yt-is transcripts')}
---"""


def promote(dry_run: bool = False) -> list[dict]:
    if not STAGING_DIR.is_dir():
        print("no staging directory")
        return []
    VAULT_CONCEPTS.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    results = []
    for staged in sorted(STAGING_DIR.glob("*.md")):
        fields, body = parse_staged_page(staged)
        if not fields:
            results.append({"page": staged.name, "status": "no-frontmatter"})
            continue
        target = VAULT_CONCEPTS / staged.name
        if target.exists():
            results.append({"page": staged.name, "status": "collision-skip"})
            continue
        if not dry_run:
            target.write_text(
                vault_frontmatter(fields, fields.get("created", today))
                + "\n" + body, encoding="utf-8")
        results.append({"page": staged.name, "status": "promoted"})
    return results


def append_log(results: list[dict]) -> None:
    promoted = [r["page"] for r in results if r["status"] == "promoted"]
    if not promoted:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    entry = (f"\n## [{today}] ingest | yt-is cluster concepts\n"
             f"Source: ef-cluster staging (wiki_from_cluster)\n"
             f"Agent: zcode\n"
             f"Notes: promoted {len(promoted)} topic-cluster concept pages "
             f"from yt-is staging into the vault (Phase 1 wiki promotion). "
             f"Verification: inferred-only (content is auto-clustered "
             f"transcript excerpts with per-claim video citations).\n"
             f"Pages: {', '.join(promoted)}\n")
    with VAULT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(entry)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Promote staged wiki pages")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    results = promote(dry_run=args.dry_run)
    for r in results:
        print(f"  {r['status']:16s} {r['page']}")
    if not args.dry_run:
        append_log(results)
        # post-write auto-link hook (same one manual wiki writes use)
        import subprocess
        for r in results:
            if r["status"] == "promoted":
                subprocess.run(
                    [sys.executable,
                     "P:/packages/.claude-marketplace/plugins/cc-skills-sdlc"
                     "/skills/wiki/scripts/wiki_after_write.py",
                     str(VAULT_CONCEPTS / r["page"])],
                    capture_output=True, timeout=60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
