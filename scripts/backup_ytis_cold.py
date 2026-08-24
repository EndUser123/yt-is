"""Cold-tier backup: newest yt-is state snapshots + wiki vault to G:.

The 03:30 YtisStateBackup task already rotates SQLite snapshots on P:
(P:/.data/yt-is/backups/{batch-status,transcripts}-*.sqlite) and copies
the newest two off-site to C:. This script adds the G: tier of the same
ladder — G: is the external USB archive disk, separate physical media
from both P: (NVMe) and C:. It also mirrors the wiki vault
(P:/.data/wiki/concepts — irreplaceable, 18 MB) so the knowledge base
survives a P: drive failure.

Idempotent: files already at the destination are skipped; re-runs are
no-ops. Loud failure if G: is absent — never silently skip a cycle.

Scheduled task "YtisColdBackup" (daily 03:35, pythonw). Manual:
    python scripts/backup_ytis_cold.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

SRC_DIR = Path("P:/.data/yt-is/backups")
WIKI_SRC = Path("P:/.data/wiki/concepts")
BACKUP_ROOT = Path(os.environ.get(
    "YTIS_COLD_BACKUP_DIR", "G:/backups"))
DB_DEST = BACKUP_ROOT / "ytis" / "db"
WIKI_DEST = BACKUP_ROOT / "wiki" / "concepts"

PATTERNS = ["batch-status-*.sqlite", "transcripts-*.sqlite"]
COPY_NEWEST = 3   # per pattern, per run
KEEP_DEST = 7     # per pattern, at destination


def _newest(pattern: str, n: int) -> list[Path]:
    return sorted(SRC_DIR.glob(pattern),
                  key=lambda p: p.stat().st_mtime, reverse=True)[:n]


def copy_db_snapshots(dry_run: bool) -> tuple[int, int]:
    copied = pruned = 0
    DB_DEST.mkdir(parents=True, exist_ok=True)
    for pattern in PATTERNS:
        for src in _newest(pattern, COPY_NEWEST):
            dest = DB_DEST / src.name
            if dest.exists():
                continue
            if dry_run:
                print(json.dumps({"action": "copy", "src": str(src),
                                  "dest": str(dest)}))
                continue
            shutil.copy2(src, dest)
            copied += 1
        versions = sorted(DB_DEST.glob(pattern),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        for old in versions[KEEP_DEST:]:
            if dry_run:
                print(json.dumps({"action": "prune", "path": str(old)}))
                continue
            old.unlink()
            pruned += 1
    return copied, pruned


def mirror_wiki(dry_run: bool) -> tuple[int, int]:
    """Latest-copy mirror: new/changed files in, gone files out."""
    copied = deleted = 0
    WIKI_DEST.mkdir(parents=True, exist_ok=True)
    src_files = {p.relative_to(WIKI_SRC): p
                 for p in WIKI_SRC.rglob("*") if p.is_file()}
    dest_files = {p.relative_to(WIKI_DEST): p
                  for p in WIKI_DEST.rglob("*") if p.is_file()}
    for rel, src in src_files.items():
        dest = WIKI_DEST / rel
        if rel in dest_files and dest.exists():
            s, d = src.stat(), dest.stat()
            if s.st_size == d.st_size and \
                    int(s.st_mtime) == int(d.st_mtime):
                continue
        if dry_run:
            print(json.dumps({"action": "wiki-copy", "path": str(rel)}))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied += 1
    for rel, dest in dest_files.items():
        if rel not in src_files:
            if dry_run:
                print(json.dumps({"action": "wiki-delete",
                                  "path": str(rel)}))
                continue
            dest.unlink()
            deleted += 1
    return copied, deleted


def backup_capture_state(dry_run: bool) -> int:
    """Capture selection/catalog (small, static). The live.dht ARCHIVE
    is deliberately NOT copied here anymore: 03:35 is always mid-capture
    (03:00-07:00) and a raw copy2 is WAL-blind — it captured none of the
    night's data (84,755 messages over two nights, verified 2026-08-24).
    The archive is backed up by scripts/dht-capture/backup_live.py AFTER
    the nightly chain's graceful app close (WAL checkpointed, verified)."""
    copied = 0
    for name in ("dht-capture-selection.json",
                 "dht-capture-catalog.json"):
        src = Path("P:/.data/yt-is") / name
        if not src.exists():
            continue
        dest_dir = BACKUP_ROOT / "ytis" / "dht-live"
        dest_dir.mkdir(parents=True, exist_ok=True)
        if dry_run:
            print(json.dumps({"action": "copy", "src": str(src),
                              "dest": str(dest_dir / name)}))
            continue
        shutil.copy2(src, dest_dir / name)
        copied += 1
    return copied


def main(argv=None) -> int:
    started = time.time()
    parser = argparse.ArgumentParser(
        description="Cold-tier backup of yt-is state + wiki vault to G:")
    parser.add_argument("--dry-run", action="store_true",
                        help="print planned actions, write nothing")
    args = parser.parse_args(argv)

    if not Path(BACKUP_ROOT.anchor).exists():
        print(json.dumps({"status": "failed",
                          "reason": f"backup drive not mounted: {BACKUP_ROOT}"}))
        return 1

    db_copied, pruned = copy_db_snapshots(args.dry_run)
    wiki_copied, wiki_deleted = mirror_wiki(args.dry_run)
    capture_copied = backup_capture_state(args.dry_run)
    print(json.dumps({
        "status": "ok",
        "db_copied": db_copied, "pruned": pruned,
        "wiki_copied": wiki_copied, "wiki_deleted": wiki_deleted,
        "capture_copied": capture_copied,
        "seconds": round(time.time() - started, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
