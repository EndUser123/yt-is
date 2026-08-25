#!/usr/bin/env python3
"""Newsletter ingestion connector: bulk email -> transcript_cache.

Pipeline (text family, RSS-adjacent): himalaya (configured CLI email
client) -> most recent envelopes -> RFC 5322 raw fetch -> stdlib email
parse -> bulk-gate -> text extraction -> transcript_cache rows with
source='newsletter' -> Evidence Fabric via ef.ingest_connectors watermark
(alias added; freshness exclusion updated to match).

Bulk gate (privacy by construction): a message is ingested ONLY if BOTH
(a) it carries a List-Unsubscribe or List-Id header — the machine
signature of bulk mail, AND (b) its sender matches the curated allowlist
at P:/.data/yt-is/config/newsletter_senders.txt (one email or @domain
per line, # comments). Receipt: a List-Unsubscribe header alone proved
insufficient — transactional personal mail (e.g. a medical
secure-message notification) carries bulk headers too. Personal 1:1 mail
and non-allowlisted bulk mail are never read into the corpus.

Idempotent: cache_key = sha1(Message-ID); re-runs store nothing new.

Usage:
    python scripts/run_newsletter_sync.py            # ingest (default 40)
    python scripts/run_newsletter_sync.py --limit 80
    python scripts/run_newsletter_sync.py --dry-run  # list candidates only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TDB = Path("P:/.data/yt-is/transcripts.sqlite")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

ALLOWLIST = Path("P:/.data/yt-is/config/newsletter_senders.txt")
MIN_TRANSCRIPT_CHARS = 500


def load_allowlist(path: Path = ALLOWLIST) -> set[str]:
    """Return lowercase emails and @domains the operator curated."""
    try:
        entries = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                entries.add(line)
        return entries
    except FileNotFoundError:
        return set()


def sender_allowed(from_email: str, allowlist: set[str]) -> bool:
    addr = (from_email or "").strip().lower()
    if not addr:
        return False
    domain = "@" + addr.split("@")[-1] if "@" in addr else ""
    return addr in allowlist or domain in allowlist


def _himalaya(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["himalaya", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=NO_WINDOW)


def himalaya_available() -> bool:
    return shutil.which("himalaya") is not None


def list_envelopes(limit: int) -> list[dict]:
    proc = _himalaya("envelope", "list", "--json", "--page-size", str(limit))
    if proc.returncode != 0:
        raise RuntimeError(
            f"himalaya envelope list failed: {(proc.stderr or '')[:200]}")
    data = json.loads(proc.stdout or "{}")
    return data.get("envelopes") or []


def fetch_raw(id_: str) -> bytes:
    proc = _himalaya("message", "read", str(id_), "--raw")
    if proc.returncode != 0:
        raise RuntimeError(
            f"himalaya message read {id_} failed: {(proc.stderr or '')[:200]}")
    return proc.stdout.encode("utf-8", errors="replace")


def parse_message(raw: bytes) -> dict | None:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    msg_id = str(msg.get("message-id") or "").strip()
    if not msg_id:
        return None
    from_hdr = msg.get("from")
    from_name, from_email = "", ""
    try:
        if from_hdr and getattr(from_hdr, "addresses", None):
            addr = from_hdr.addresses[0]
            from_name = str(getattr(addr, "display_name", "") or "")
            from_email = str(getattr(addr, "addr_spec", "") or "")
    except Exception:
        from_name, from_email = str(from_hdr or ""), ""
    record = {
        "message_id": msg_id,
        "subject": str(msg.get("subject") or "").strip(),
        "from_name": from_name,
        "from_email": from_email,
        "date": str(msg.get("date") or ""),
        "list_unsub": bool(msg.get("list-unsubscribe")),
        "list_id": bool(msg.get("list-id")),
        "text": "",
    }
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return record
    try:
        content = body.get_content()
    except Exception:
        return record
    if body.get_content_type() == "text/html":
        try:
            import trafilatura
            extracted = trafilatura.extract(content) or ""
        except Exception:
            extracted = ""
        record["text"] = extracted.strip()
    else:
        record["text"] = str(content).strip()
    return record


def is_bulk(rec: dict) -> bool:
    # Machine signature of bulk mail ONLY (necessary, not sufficient).
    return bool(rec.get("list_unsub") or rec.get("list_id"))


def to_transcript(rec: dict) -> str:
    parts = ["Newsletter: " + (rec["from_name"] or rec["from_email"]),
             "Subject: " + rec["subject"],
             f"From: {rec['from_name']} <{rec['from_email']}>",
             f"Date: {rec['date']}",
             "", rec["text"]]
    return "\n".join(p for p in parts if p is not None)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def store_once(rec: dict, db_path: Path) -> bool:
    key = hashlib.sha1(rec["message_id"].encode("utf-8")).hexdigest()[:20]
    cache_key = f"newsletter:{key}"
    doc_id = f"newsletter_{key}"
    transcript = to_transcript(rec)
    if len(transcript) < MIN_TRANSCRIPT_CHARS:
        return False
    for attempt in range(3):
        try:
            conn = _connect(db_path)
            try:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM transcript_cache WHERE cache_key = ?",
                    (cache_key,)).fetchone()[0]
                if existing:
                    return False
                conn.execute(
                    """INSERT OR REPLACE INTO transcript_cache
                       (cache_key, video_id, lang, source, transcript,
                        metadata_json, cached_at, terminal_id)
                       VALUES (?, ?, 'en', 'newsletter', ?, ?, ?, 'newsletter')""",
                    (cache_key, doc_id, transcript,
                     json.dumps({"from": rec["from_name"],
                                 "from_email": rec["from_email"],
                                 "subject": rec["subject"],
                                 "date": rec["date"],
                                 "message_id": rec["message_id"]}),
                     datetime.now(timezone.utc).isoformat()))
                conn.commit()
                return True
            finally:
                conn.close()
        except sqlite3.OperationalError:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    return False


def sync(limit: int = 40, dry_run: bool = False,
         db_path: Path | None = None,
         allowlist_path: Path = ALLOWLIST) -> dict:
    if not himalaya_available():
        return {"skipped": "himalaya not on PATH"}
    db_path = db_path or TDB
    allowlist = load_allowlist(allowlist_path)
    summary = {"scanned": 0, "allowed_bulk": 0, "stored": 0,
               "already_seen": 0, "skipped_personal": 0,
               "skipped_bulk_not_allowed": 0, "skipped_short": 0,
               "allowlist_entries": len(allowlist),
               "dry_run": dry_run, "candidates": []}
    envelopes = list_envelopes(limit)
    for env in envelopes:
        summary["scanned"] += 1
        try:
            rec = parse_message(fetch_raw(env["id"]))
        except Exception as exc:
            summary.setdefault("errors", []).append(f"id {env['id']}: {exc}")
            continue
        if rec is None:
            continue
        if not is_bulk(rec):
            summary["skipped_personal"] += 1
            continue
        if not sender_allowed(rec["from_email"], allowlist):
            summary["skipped_bulk_not_allowed"] += 1
            continue
        summary["allowed_bulk"] += 1
        if dry_run:
            summary["candidates"].append(
                {"subject": rec["subject"][:80], "from": rec["from_email"],
                 "chars": len(rec["text"])})
            continue
        stored = store_once(rec, db_path)
        if stored:
            summary["stored"] += 1
        elif len(to_transcript(rec)) < MIN_TRANSCRIPT_CHARS:
            summary["skipped_short"] += 1
        else:
            summary["already_seen"] += 1
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=40,
                    help="how many recent envelopes to scan (default 40)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list newsletter candidates, write nothing")
    ap.add_argument("--db-path", type=Path, default=TDB,
                    help="override transcript cache path (tests)")
    args = ap.parse_args(argv)
    try:
        summary = sync(limit=args.limit, dry_run=args.dry_run,
                       db_path=args.db_path)
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
