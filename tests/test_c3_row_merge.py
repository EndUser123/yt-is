"""C3 durable row-merge policy falsifier tests.

Each test exercises a specific C3 contract rule and verifies the fix
prevents the bug described in the acceptance findings (INT-001..008).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from csf.batch_status import _BatchStatusStorage


@pytest.fixture
def storage(tmp_path):
    """Fresh _BatchStatusStorage with a temp DB."""
    return _BatchStatusStorage(db_path=tmp_path / "test.sqlite")


# --- INT-001: set_channel_metadata packs None kwargs ----------------------------


def test_int001_partial_metadata_does_not_wipe_keywords(storage):
    """Falsifier: After keywords backfill, upsert_channel(last_checked=now) must NOT clear keywords."""
    # Step 1: Backfill keywords + subscriber_count
    storage.set_channel_metadata(
        "https://www.youtube.com/channel/UC_test01",
        channel_id="UC_test01",
        keywords="python, coding, tutorials",
        subscriber_count=50000,
    )
    # Verify they're stored
    row = storage._get_conn().execute(
        "SELECT keywords, subscriber_count FROM channel_metadata WHERE channel_id = ?",
        ("UC_test01",),
    ).fetchone()
    assert row[0] == "python, coding, tutorials"
    assert row[1] == 50000

    # Step 2: Partial update — only last_checked (C3 falsifier)
    storage.set_channel_metadata(
        "https://www.youtube.com/channel/UC_test01",
        channel_id="UC_test01",
        last_checked="2026-07-19T12:00:00Z",
    )

    # Step 3: keywords + subscriber_count must survive
    row = storage._get_conn().execute(
        "SELECT keywords, subscriber_count FROM channel_metadata WHERE channel_id = ?",
        ("UC_test01",),
    ).fetchone()
    assert row[0] == "python, coding, tutorials", "INT-001 regression: keywords wiped by partial update"
    assert row[1] == 50000, "INT-001 regression: subscriber_count wiped by partial update"


def test_int001_partial_metadata_does_not_wipe_custom_url(storage):
    """Same falsifier for custom_url field."""
    storage.set_channel_metadata(
        "https://www.youtube.com/channel/UC_test02",
        channel_id="UC_test02",
        custom_url="https://www.youtube.com/c/TestChannel",
        channel_title="Test Channel",
    )
    # Partial update
    storage.set_channel_metadata(
        "https://www.youtube.com/channel/UC_test02",
        channel_id="UC_test02",
        video_count_estimate=100,
    )
    row = storage._get_conn().execute(
        "SELECT custom_url, channel_title FROM channel_metadata WHERE channel_id = ?",
        ("UC_test02",),
    ).fetchone()
    assert row[0] == "https://www.youtube.com/c/TestChannel", "INT-001: custom_url wiped"
    assert row[1] == "Test Channel", "INT-001: channel_title wiped"


# --- INT-002: upsert_channel SELECT omits keywords/custom_url ------------------


def test_int002_upsert_preserves_keywords_not_in_update(storage):
    """Direct upsert_channel call without keywords must preserve existing."""
    storage.upsert_channel(
        "https://www.youtube.com/channel/UC_test03",
        channel_id="UC_test03",
        keywords="gaming, esports",
    )
    # Now call upsert without keywords (only video_count)
    storage.upsert_channel(
        "https://www.youtube.com/channel/UC_test03",
        channel_id="UC_test03",
        video_count_estimate=200,
    )
    row = storage._get_conn().execute(
        "SELECT keywords, video_count_estimate FROM channel_metadata WHERE channel_id = ?",
        ("UC_test03",),
    ).fetchone()
    assert row[0] == "gaming, esports", "INT-002: keywords wiped by upsert without keywords"
    assert row[1] == 200, "INT-002: video_count_estimate not updated"


# --- INT-004: sticky complete + failure_reason ---------------------------------


def test_int004_mark_failed_after_complete_does_not_overwrite_diagnostics(storage):
    """Falsifier: mark_failed after mark_complete must not leave contradictory pair."""
    video_id = "vid_complete_001"
    # Mark complete
    storage.set_status(video_id, "complete", source="src1", last_stage="notebooklm")
    # Verify complete
    row = storage._get_conn().execute(
        "SELECT status, failure_reason, last_stage FROM analysis_status WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    assert row[0] == "complete"
    assert row[1] is None  # no failure_reason
    assert row[2] == "notebooklm"

    # Now try to mark failed (should NOT overwrite diagnostics)
    storage.set_status(
        video_id, "failed", source="src1",
        failure_reason="no_transcript", last_stage="selenium",
    )
    row = storage._get_conn().execute(
        "SELECT status, failure_reason, last_stage FROM analysis_status WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    assert row[0] == "complete", "INT-004: status regressed from complete to failed"
    assert row[1] is None, "INT-004: failure_reason set on a complete video (contradictory)"
    assert row[2] == "notebooklm", "INT-004: last_stage overwritten on complete video"


def test_int004_mark_complete_still_works_after_failed(storage):
    """Normal flow: failed → complete must transition cleanly."""
    video_id = "vid_flow_001"
    storage.set_status(video_id, "failed", failure_reason="timeout", last_stage="selenium")
    storage.set_status(video_id, "complete", last_stage="notebooklm", quality_metrics='{"like_rate":0.05}')
    row = storage._get_conn().execute(
        "SELECT status, failure_reason FROM analysis_status WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    assert row[0] == "complete"
    assert row[1] == "timeout", "failure_reason should still be visible (was set before complete)"


# --- INT-008: block_channel hard-deletes ---------------------------------------


def test_int008_block_channel_preserves_metadata(storage):
    """Falsifier: block_channel must NOT destroy channel_metadata rows."""
    storage.set_channel_metadata(
        "https://www.youtube.com/channel/UC_blocked01",
        channel_id="UC_blocked01",
        channel_title="Blocked Channel",
        subscriber_count=1000,
    )
    # Block the channel
    storage.block_channel("https://www.youtube.com/channel/UC_blocked01")

    # Metadata must still exist
    row = storage._get_conn().execute(
        "SELECT channel_title, subscriber_count FROM channel_metadata WHERE channel_id = ?",
        ("UC_blocked01",),
    ).fetchone()
    assert row is not None, "INT-008: channel_metadata deleted by block_channel"
    assert row[0] == "Blocked Channel"
    assert row[1] == 1000


def test_int008_block_channel_preserves_analysis_status(storage):
    """Falsifier: block_channel must NOT destroy analysis_status rows."""
    video_id = "vid_blocked_001"
    storage.set_status(video_id, "complete", source="https://www.youtube.com/channel/UC_blocked02")
    storage.block_channel("https://www.youtube.com/channel/UC_blocked02")

    # analysis_status row must still exist
    row = storage._get_conn().execute(
        "SELECT status FROM analysis_status WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    assert row is not None, "INT-008: analysis_status deleted by block_channel"
    assert row[0] == "complete"


def test_int008_block_channel_adds_to_blocklist(storage):
    """block_channel still adds to the blocklist table."""
    storage.set_channel_metadata(
        "https://www.youtube.com/channel/UC_blocked03",
        channel_id="UC_blocked03",
    )
    storage.block_channel("https://www.youtube.com/channel/UC_blocked03")
    assert storage.is_channel_blocked("https://www.youtube.com/channel/UC_blocked03")


# --- INT-003: promote_batch_status_db field merge ------------------------------


def test_int003_promote_does_not_clobber_live_metadata(tmp_path):
    """Falsifier: sparse promote cannot null live subscriber_count."""
    # Create source (staging) DB
    source = _BatchStatusStorage(db_path=tmp_path / "source.sqlite")
    source.set_channel_metadata(
        "https://www.youtube.com/channel/UC_promote01",
        channel_id="UC_promote01",
        channel_title="Source Channel",
    )
    # Source has NO subscriber_count (sparse staging row)

    # Create dest (live) DB with subscriber_count populated
    dest = _BatchStatusStorage(db_path=tmp_path / "dest.sqlite")
    dest.set_channel_metadata(
        "https://www.youtube.com/channel/UC_promote01",
        channel_id="UC_promote01",
        subscriber_count=75000,
        keywords="tech, reviews",
    )

    # Promote source → dest
    from csf.batch_status import promote_batch_status_db
    promoted = promote_batch_status_db(tmp_path / "source.sqlite", tmp_path / "dest.sqlite")
    assert promoted >= 1

    # Live row must NOT be clobbered by sparse staging data
    row = dest._get_conn().execute(
        "SELECT subscriber_count, keywords, channel_title FROM channel_metadata WHERE channel_id = ?",
        ("UC_promote01",),
    ).fetchone()
    assert row[0] == 75000, "INT-003: subscriber_count nulled by sparse promote"
    assert row[1] == "tech, reviews", "INT-003: keywords nulled by sparse promote"
    assert row[2] == "Source Channel", "INT-003: channel_title not merged from staging"


def test_int003_promote_merges_non_null_fields(tmp_path):
    """Staging non-NULL fields DO overwrite live values (correct merge semantics)."""
    source = _BatchStatusStorage(db_path=tmp_path / "source.sqlite")
    source.set_channel_metadata(
        "https://www.youtube.com/channel/UC_promote02",
        channel_id="UC_promote02",
        channel_title="Updated Title",
        subscriber_count=99999,
    )
    dest = _BatchStatusStorage(db_path=tmp_path / "dest.sqlite")
    dest.set_channel_metadata(
        "https://www.youtube.com/channel/UC_promote02",
        channel_id="UC_promote02",
        channel_title="Old Title",
        subscriber_count=100,
        keywords="old keywords",
    )

    from csf.batch_status import promote_batch_status_db
    promote_batch_status_db(tmp_path / "source.sqlite", tmp_path / "dest.sqlite")

    row = dest._get_conn().execute(
        "SELECT channel_title, subscriber_count, keywords FROM channel_metadata WHERE channel_id = ?",
        ("UC_promote02",),
    ).fetchone()
    # Non-NULL staging values overwrite
    assert row[0] == "Updated Title", "non-null staging field should overwrite"
    assert row[1] == 99999, "non-null staging field should overwrite"
    # NULL staging values preserve existing
    assert row[2] == "old keywords", "null staging field should preserve live value"