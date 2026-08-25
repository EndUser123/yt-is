"""Connector abstraction: one interface for every content source.

Each source (Reddit, HN, RSS, GitHub, Discord/DHT, …) implements the
same contract: discover new items, store them as documents in
transcript_cache, report counts. The registry drives them all from one
entry point (`ytis sync` / run_all_syncs), and the shared EF ingestion
indexes whatever any connector stored — a new source is a small class,
not a new pipeline.

Design notes:
  - Connectors wrap the proven per-source scripts rather than replacing
    them wholesale; refactor internals at will behind `sync()`.
  - Discord has TWO sources (bot API + DHT archive); DHT is primary
    (operator has no server), bot-API stays parked until a server admin
    approves an invite. Registering both is intentional — the registry
    runs whatever is configured/available.
  - Failures are isolated: one connector error never blocks the rest.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Connector:
    name: str                     # registry key + display name
    sync: Callable[[], dict]      # returns {"new": int, ...}
    available: Callable[[], bool] = lambda: True
    description: str = ""
    # CLI passthrough (scripts with their own argparse, e.g. --add)
    script: Path | None = None


def _run_script(script_name: str) -> dict:
    """Run a connector script and parse its 'Done: N new' tail."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / script_name)],
        cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    new = 0
    for line in (proc.stdout or "").splitlines():
        if "Done:" in line:
            digits = "".join(ch for ch in line.split("Done:")[1].split()[0]
                             if ch.isdigit())
            new = int(digits or 0)
    return {"new": new, "returncode": proc.returncode,
            "tail": (proc.stdout or proc.stderr or "")[-200:]}


def _discord_available() -> bool:
    import os
    from csf.paths import load_workspace_env
    load_workspace_env()
    return bool(os.environ.get("DISCORD_BOT_TOKEN"))


def _dht_available() -> bool:
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import run_dht_ingest
        return len(run_dht_ingest.discover_archives()) > 0
    except Exception:
        return False


def _newsletter_available() -> bool:
    import shutil
    return shutil.which("himalaya") is not None


REGISTRY: list[Connector] = [
    Connector(
        name="reddit",
        sync=lambda: _run_script("run_reddit_sync.py"),
        description="Posts + comments from tracked subreddits"),
    Connector(
        name="hackernews",
        sync=lambda: _run_script("run_hn_sync.py"),
        description="Top stories via Algolia API"),
    Connector(
        name="rss",
        sync=lambda: _run_script("run_rss_sync.py"),
        description="Blog/article feeds (full-text via trafilatura)"),
    Connector(
        name="newsletter",
        sync=lambda: _run_script("run_newsletter_sync.py"),
        available=_newsletter_available,
        description="Bulk email newsletters via himalaya "
                    "(List-Unsubscribe gated; personal mail excluded)"),
    Connector(
        name="github",
        sync=lambda: _run_script("run_github_sync.py"),
        description="Repo READMEs + releases via gh CLI"),
    Connector(
        name="discord-dht",
        sync=lambda: _run_script("run_dht_ingest.py"),
        available=_dht_available,
        description="Discord History Tracker archive (primary Discord source)"),
    Connector(
        name="discord-bot",
        sync=lambda: _run_script("run_discord_sync.py"),
        available=_discord_available,
        description="Discord bot API (parked until a server admin "
                    "approves the bot invite)"),
]


def sync_all(names: list[str] | None = None) -> dict[str, dict]:
    """Run every available connector (or the named subset); then index
    everything new through the shared EF ingestion."""
    results: dict[str, dict] = {}
    for c in REGISTRY:
        if names and c.name not in names:
            continue
        try:
            if not c.available():
                results[c.name] = {"skipped": "unavailable"}
                continue
            results[c.name] = c.sync()
        except Exception as e:
            results[c.name] = {"error": str(e)[:200]}  # isolated failure
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ef.ingest_connectors"],
            cwd=str(REPO), capture_output=True, text=True, timeout=3600)
        results["ef_ingest"] = {"returncode": proc.returncode,
                                "tail": (proc.stdout or "")[-200:]}
    except Exception as e:
        results["ef_ingest"] = {"error": str(e)[:200]}
    return results


def list_connectors() -> list[dict]:
    return [{"name": c.name, "description": c.description,
             "available": bool(c.available())} for c in REGISTRY]
