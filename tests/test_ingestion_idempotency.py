"""Tests for idempotent artifact publication (csf/ingestion.py, U-06)."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from csf.batch_status import V2_MIGRATION_SQL_PATH
from csf.ingestion import publish_artifact


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "batch.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(V2_MIGRATION_SQL_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return path


def test_first_publish_writes_receipt_with_version_1(db: Path):
    result = publish_artifact("vidA", "visual_frames", "sha256:abc", db_path=db)
    assert result["published"] is True
    assert result["version"] == 1
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT video_id, downstream, content_hash, version FROM ingestion_receipts").fetchall()
    conn.close()
    assert rows == [("vidA", "visual_frames", "sha256:abc", 1)]


def test_duplicate_publish_is_noop(db: Path):
    first = publish_artifact("vidA", "visual_frames", "sha256:abc", db_path=db)
    second = publish_artifact("vidA", "visual_frames", "sha256:abc", db_path=db)
    assert first["published"] is True
    assert second["published"] is False
    assert second["version"] == first["version"]
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM ingestion_receipts").fetchone()[0]
    conn.close()
    assert count == 1


def test_changed_content_gets_new_receipt_and_version(db: Path):
    publish_artifact("vidA", "visual_frames", "sha256:abc", db_path=db)
    result = publish_artifact("vidA", "visual_frames", "sha256:def", db_path=db)
    assert result["published"] is True
    assert result["version"] == 2


def test_downstreams_are_independent(db: Path):
    publish_artifact("vidA", "visual_frames", "sha256:abc", db_path=db)
    result = publish_artifact("vidA", "wiki_sync", "sha256:abc", db_path=db)
    assert result["published"] is True
    assert result["version"] == 1


def test_empty_arguments_rejected(db: Path):
    with pytest.raises(ValueError):
        publish_artifact("", "visual_frames", "sha256:abc", db_path=db)
    with pytest.raises(ValueError):
        publish_artifact("vidA", "", "sha256:abc", db_path=db)
    with pytest.raises(ValueError):
        publish_artifact("vidA", "visual_frames", "", db_path=db)
