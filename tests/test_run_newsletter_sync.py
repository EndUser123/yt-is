"""Tests for the newsletter connector (gate, allowlist, idempotency).

No network, no himalaya: subprocess calls are monkeypatched with fixture
MIME messages; the cache is a tmp sqlite built from the live schema.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_newsletter_sync as nls  # noqa: E402

NEWSLETTER = (
    b"From: Alex Wilhelm <alex@cautiousoptimism.news>\r\n"
    b"To: op@example.com\r\n"
    b"Subject: American AI isn't losing the price war\r\n"
    b"Date: Mon, 25 Aug 2026 16:26:00 +0000\r\n"
    b"Message-ID: <nl-1@cautiousoptimism.news>\r\n"
    b"List-Unsubscribe: <https://cautiousoptimism.news/unsub>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"American AI isn't losing the price war. " + b"x" * 600 + b"\r\n"
)

PERSONAL = (
    b"From: Clinic <noreply@medeohealth.com>\r\n"
    b"To: op@example.com\r\n"
    b"Subject: secure message\r\n"
    b"Date: Mon, 25 Aug 2026 17:29:00 +0000\r\n"
    b"Message-ID: <p-1@medeohealth.com>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"personal medical correspondence " + b"y" * 300 + b"\r\n"
)

# Bulk headers BUT not allowlisted: transactional/notify mail.
BULK_NOT_ALLOWED = NEWSLETTER.replace(
    b"<nl-1@cautiousoptimism.news>", b"<t-1@transactions.example.com>"
).replace(b"alex@cautiousoptimism.news", b"no-reply@transactions.example.com")


def _fake_himalaya(messages: dict[str, bytes]):
    def runner(*cmd_args, **kwargs):
        cmd = ["himalaya", *cmd_args]
        if cmd[1:4] == ["envelope", "list", "--json"]:
            envelopes = [{"id": str(i)} for i in range(1, len(messages) + 1)]
            body = json.dumps({"envelopes": envelopes})
            return subprocess.CompletedProcess(cmd, 0, body, "")
        if cmd[1:3] == ["message", "read"]:
            raw = messages.get(cmd[3], b"")
            return subprocess.CompletedProcess(
                cmd, 0, raw.decode("utf-8", errors="replace"), "")
        return subprocess.CompletedProcess(cmd, 1, "", "unexpected")
    return runner


@pytest.fixture
def cache_db(tmp_path):
    p = tmp_path / "transcripts.sqlite"
    conn = sqlite3.connect(str(p))
    conn.execute(
        """CREATE TABLE transcript_cache (
             cache_key TEXT PRIMARY KEY, video_id TEXT, lang TEXT,
             source TEXT, transcript TEXT, metadata_json TEXT,
             cached_at TEXT, terminal_id TEXT)""")
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def allowlist(tmp_path):
    p = tmp_path / "senders.txt"
    p.write_text("# curated\n@cautiousoptimism.news\n", encoding="utf-8")
    return p


def test_gate_personal_and_unallowed_bulk_excluded(
        cache_db, allowlist, monkeypatch):
    monkeypatch.setattr(nls, "_himalaya", _fake_himalaya({
        "1": NEWSLETTER, "2": PERSONAL, "3": BULK_NOT_ALLOWED}))
    summary = nls.sync(limit=10, db_path=cache_db, allowlist_path=allowlist)
    assert summary["scanned"] == 3
    assert summary["stored"] == 1
    assert summary["skipped_personal"] == 1
    assert summary["skipped_bulk_not_allowed"] == 1
    row = sqlite3.connect(str(cache_db)).execute(
        "select source, video_id from transcript_cache").fetchall()
    assert row == [("newsletter", row[0][1])]
    assert row[0][1].startswith("newsletter_")


def test_idempotent_second_run_stores_nothing(
        cache_db, allowlist, monkeypatch):
    fake = _fake_himalaya({"1": NEWSLETTER})
    monkeypatch.setattr(nls, "_himalaya", fake)
    first = nls.sync(limit=10, db_path=cache_db, allowlist_path=allowlist)
    second = nls.sync(limit=10, db_path=cache_db, allowlist_path=allowlist)
    assert first["stored"] == 1 and second["stored"] == 0
    assert second["already_seen"] == 1


def test_empty_allowlist_ingests_nothing(cache_db, tmp_path, monkeypatch):
    monkeypatch.setattr(nls, "_himalaya", _fake_himalaya({"1": NEWSLETTER}))
    summary = nls.sync(limit=10, db_path=cache_db,
                       allowlist_path=tmp_path / "missing.txt")
    assert summary["stored"] == 0
    assert summary["skipped_bulk_not_allowed"] == 1


def test_short_newsletter_skipped(cache_db, allowlist, monkeypatch):
    short = NEWSLETTER.replace(b"x" * 600, b"x" * 30)
    monkeypatch.setattr(nls, "_himalaya", _fake_himalaya({"1": short}))
    summary = nls.sync(limit=10, db_path=cache_db, allowlist_path=allowlist)
    assert summary["stored"] == 0
    assert summary["skipped_short"] == 1
