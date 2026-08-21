"""GitHub ingestion — repos, READMEs, and release notes into yt-is.

Uses the authenticated `gh` CLI (no extra credentials, generous rate
limits). Each tracked repo contributes one document per release plus its
README; docs land in transcript_cache (source='github') and flow into
the search index via the standard connector ingestion.

Usage:
    python scripts/run_github_sync.py --add ollama/ollama
    python scripts/run_github_sync.py --list
    python scripts/run_github_sync.py
"""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env

DB = Path("P:/.data/yt-is/batch_status.sqlite")
TDB = Path("P:/.data/yt-is/transcripts.sqlite")
RELEASES_PER_REPO = 10
REQUEST_DELAY_S = 1.0


def _retry_locked(fn, attempts=4, delay_s=5.0):
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay_s)


def _rw(path):
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def gh_api(endpoint: str) -> dict | list | None:
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def ensure_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS github_repos (
            repo TEXT PRIMARY KEY,
            added_at TEXT NOT NULL,
            last_synced TEXT,
            total_docs INTEGER DEFAULT 0
        );
    """)
    conn.commit()


def add_repo(repo: str):
    repo = repo.strip().strip("/").replace("https://github.com/", "")
    info = gh_api(f"repos/{repo}")
    if not info:
        print(f"  not found or inaccessible: {repo}")
        return
    conn = _rw(DB)
    ensure_table(conn)
    conn.execute(
        "INSERT OR IGNORE INTO github_repos (repo, added_at) VALUES (?, ?)",
        (repo, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    print(f"  Added {repo} — {info.get('description', '')[:70]}")


def _store_doc_once(repo: str, doc_id: str, title: str, body: str,
                    url: str, published: str) -> bool:
    if not body or len(body) < 100:
        return False
    cache_key = f"github:{doc_id}"
    tdb = _rw(TDB)
    if tdb.execute("SELECT 1 FROM transcript_cache WHERE cache_key = ?",
                   (cache_key,)).fetchone():
        tdb.close()
        return False
    transcript = f"Repo: {repo}\nTitle: {title}\nURL: {url}\n\n{body[:50000]}"
    tdb.execute(
        """INSERT OR REPLACE INTO transcript_cache
           (cache_key, video_id, lang, source, transcript, metadata_json,
            cached_at, terminal_id)
           VALUES (?, ?, 'en', 'github', ?, ?, ?, 'github')""",
        (cache_key, f"github_{doc_id}", transcript,
         json.dumps({"repo": repo, "title": title, "link": url,
                     "published": published, "kind": doc_id.split("/")[-1]}),
         datetime.now(timezone.utc).isoformat()))
    tdb.commit()
    tdb.close()
    return True


def store_doc(repo, doc_id, title, body, url, published):
    return _retry_locked(
        lambda: _store_doc_once(repo, doc_id, title, body, url, published))


def sync_repo(repo: str) -> dict:
    new_docs = 0

    readme = gh_api(f"repos/{repo}/readme")
    time.sleep(REQUEST_DELAY_S)
    if readme and readme.get("content"):
        try:
            body = base64.b64decode(readme["content"]).decode("utf-8", "replace")
        except Exception:
            body = ""
        if store_doc(repo, f"{repo}/readme", f"{repo} — README", body,
                     f"https://github.com/{repo}", ""):
            new_docs += 1

    releases = gh_api(f"repos/{repo}/releases?per_page={RELEASES_PER_REPO}")
    time.sleep(REQUEST_DELAY_S)
    for rel in (releases or [])[:RELEASES_PER_REPO]:
        if not isinstance(rel, dict):
            continue
        title = f"{repo} — release {rel.get('tag_name', '?')}"
        body = rel.get("body") or ""
        if store_doc(repo, f"{repo}/releases/{rel.get('tag_name', rel.get('id'))}",
                     title, body, rel.get("html_url", ""),
                     rel.get("published_at", "")):
            new_docs += 1

    conn = _rw(DB)
    ensure_table(conn)
    conn.execute(
        """INSERT INTO github_repos (repo, added_at, last_synced, total_docs)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(repo) DO UPDATE SET
             last_synced = excluded.last_synced,
             total_docs = total_docs + excluded.total_docs""",
        (repo, datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat(), new_docs))
    conn.commit()
    conn.close()
    return {"repo": repo, "new": new_docs}


def main(argv=None):
    load_workspace_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", default=None)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    if args.add:
        add_repo(args.add)
        return 0
    conn = _rw(DB)
    ensure_table(conn)
    repos = [r[0] for r in conn.execute(
        "SELECT repo FROM github_repos ORDER BY added_at").fetchall()]
    conn.close()
    if args.list or not repos:
        if not repos:
            print("No repos tracked. Use --add owner/repo")
        for r in repos:
            print(f"  {r}")
        return 0

    print(f"Syncing {len(repos)} GitHub repos…")
    total = 0
    for r in repos:
        out = sync_repo(r)
        total += out["new"]
        print(f"  {r}: {out['new']} new docs")
    print(f"Done: {total} new docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
