"""Tests for harvested-document ingest classes on /ingest-extension
(work packet 20260824e Y1 / task 6c): article + pdf rows are E-class
transcript_cache entries, idempotent per url, never curated authority."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from ef.warm_query_service import ingest_extension

CREATE_SQL = """
CREATE TABLE transcript_cache (
    cache_key TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    lang TEXT,
    source TEXT,
    transcript TEXT,
    metadata_json TEXT,
    cached_at TEXT,
    terminal_id TEXT
)
"""


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "transcripts.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(CREATE_SQL)
    conn.commit()
    conn.close()
    return path


def test_article_ingest_saves_and_is_idempotent(db: Path):
    payload = {"kind": "article", "url": "https://example.com/post",
               "title": "A post", "text": "body text " * 20}
    status, body = ingest_extension(payload, transcripts_db=db)
    assert status == 200 and body["status"] == "saved"
    status2, body2 = ingest_extension(payload, transcripts_db=db)
    assert status2 == 200 and body2["status"] == "already_present"

    conn = sqlite3.connect(db)
    row = conn.execute(
        "select video_id, source, metadata_json from transcript_cache"
    ).fetchone()
    conn.close()
    assert row[1] == "extension-article"
    meta = json.loads(row[2])
    assert meta["kind"] == "article" and meta["origin"] == "extension"
    assert row[0].startswith("doc-")


def test_pdf_ingest_carries_page_count(db: Path):
    payload = {"kind": "pdf", "url": "https://example.com/paper.pdf",
               "title": "Paper", "text": "pdf text " * 20, "pageCount": 7}
    status, body = ingest_extension(payload, transcripts_db=db)
    assert status == 200 and body["status"] == "saved"
    conn = sqlite3.connect(db)
    meta = json.loads(conn.execute(
        "select metadata_json from transcript_cache").fetchone()[0])
    conn.close()
    assert meta["kind"] == "pdf" and meta["pageCount"] == 7


def test_changed_content_conflicts(db: Path):
    base = {"kind": "article", "url": "https://example.com/x",
            "title": "x", "text": "one " * 50}
    ingest_extension(base, transcripts_db=db)
    status, body = ingest_extension({**base, "text": "two " * 50},
                                    transcripts_db=db)
    assert status == 409 and body["error"] == "existing_document_differs"


def test_malformed_and_unsupported_kinds_rejected(db: Path):
    assert ingest_extension({"kind": "article", "url": "not-a-url",
                             "text": "x"}, transcripts_db=db)[0] == 400
    assert ingest_extension({"kind": "pdf", "url": "https://a.com/b",
                             "text": "   "}, transcripts_db=db)[0] == 400
    assert ingest_extension({"kind": "newsletter", "url": "https://a.com/c",
                             "text": "x"}, transcripts_db=db)[0] == 400
