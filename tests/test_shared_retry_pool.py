"""Tests for the shared NotebookLM retry pool."""

from csf.shared_retry_pool import (
    claim_ready,
    enqueue,
    mark_complete,
    mark_permanent_failure,
    pending_count,
    reset_pool,
    reschedule,
)


def _select(conn, video_id):
    row = conn.execute(
        "SELECT status, claimed_by, retry_count FROM shared_retry_pool WHERE video_id=?",
        (video_id,),
    ).fetchone()
    return row


def test_enqueue_and_claim_ready_round_trip():
    reset_pool()
    assert enqueue("dQw4w9WgXcQ", retry_count=0, delay_s=0.0, last_error="test") is True
    claimed = claim_ready(limit=5, claimant_id="worker-01")
    assert len(claimed) == 1
    assert claimed[0].video_id == "dQw4w9WgXcQ"
    assert claimed[0].retry_count == 0
    assert claimed[0].status == "claimed"
    assert pending_count() == 0
    assert mark_complete("dQw4w9WgXcQ") is True


def test_reschedule_puts_item_back_into_pending_pool():
    reset_pool()
    assert enqueue("dQw4w9WgXcQ", retry_count=1, delay_s=0.0, last_error="first") is True
    claimed = claim_ready(limit=5, claimant_id="worker-01")
    assert len(claimed) == 1
    assert reschedule("dQw4w9WgXcQ", retry_count=2, delay_s=0.0, last_error="again") is True
    claimed_again = claim_ready(limit=5, claimant_id="worker-02")
    assert len(claimed_again) == 1
    assert claimed_again[0].video_id == "dQw4w9WgXcQ"
    assert claimed_again[0].retry_count == 2


# --- C1 trust-floor guards ---


def test_enqueue_does_not_steal_live_claim():
    """A non-stale claim must survive a re-enqueue attempt."""
    reset_pool()
    enqueue("dQw4w9WgXcQ", retry_count=0, delay_s=0.0, last_error="first")
    claim_ready(limit=5, claimant_id="worker-01")
    # Another process tries to re-enqueue the same video_id with a fresh error.
    # C1: live claim wins; the row's status/claimant/claimed_at are unchanged.
    enqueued = enqueue(
        "dQw4w9WgXcQ",
        retry_count=1,
        delay_s=0.0,
        last_error="stale re-enqueue",
        stale_claim_s=900.0,
    )
    assert enqueued is False
    from csf.shared_retry_pool import _connect

    with _connect() as conn:
        status, claimant, retry_count = _select(conn, "dQw4w9WgXcQ")
    assert status == "claimed"
    assert claimant == "worker-01"
    assert retry_count == 0  # not stomped by the re-enqueue


def test_enqueue_steals_stale_claim():
    """A claim past stale_claim_s must be reclaimable by a fresh enqueue."""
    reset_pool()
    enqueue("dQw4w9WgXcQ", retry_count=0, delay_s=0.0, last_error="first")
    claim_ready(limit=5, claimant_id="worker-01")
    # stale_claim_s=0 forces the existing claim to be treated as stale
    enqueued = enqueue(
        "dQw4w9WgXcQ",
        retry_count=2,
        delay_s=0.0,
        last_error="stale reclaim",
        stale_claim_s=0.0,
    )
    assert enqueued is True
    from csf.shared_retry_pool import _connect

    with _connect() as conn:
        status, claimant, retry_count = _select(conn, "dQw4w9WgXcQ")
    assert status == "pending"
    assert (claimant or "") == ""
    assert retry_count == 2


def test_enqueue_does_not_resurrect_completed():
    """Terminal state (completed) must survive a late re-enqueue."""
    reset_pool()
    enqueue("dQw4w9WgXcQ", retry_count=0, delay_s=0.0, last_error="first")
    claim_ready(limit=5, claimant_id="worker-01")
    mark_complete("dQw4w9WgXcQ", claimant_id="worker-01")
    enqueued = enqueue(
        "dQw4w9WgXcQ",
        retry_count=5,
        delay_s=0.0,
        last_error="late retry",
        stale_claim_s=900.0,
    )
    assert enqueued is False
    from csf.shared_retry_pool import _connect

    with _connect() as conn:
        status, _claimant, retry_count = _select(conn, "dQw4w9WgXcQ")
    assert status == "completed"
    assert retry_count == 0


def test_mark_complete_requires_claimant():
    """Wrong claimant must NOT clobber the live claim."""
    reset_pool()
    enqueue("dQw4w9WgXcQ", retry_count=0, delay_s=0.0, last_error="first")
    claim_ready(limit=5, claimant_id="worker-01")
    assert mark_complete("dQw4w9WgXcQ", claimant_id="worker-02") is False
    assert mark_complete("dQw4w9WgXcQ", claimant_id="worker-01") is True


def test_mark_permanent_failure_requires_claimant():
    """Wrong claimant must NOT permanent-fail the live claim."""
    reset_pool()
    enqueue("dQw4w9WgXcQ", retry_count=0, delay_s=0.0, last_error="first")
    claim_ready(limit=5, claimant_id="worker-01")
    assert mark_permanent_failure(
        "dQw4w9WgXcQ", "wrong claimant", claimant_id="worker-02"
    ) is False
    assert (
        mark_permanent_failure(
            "dQw4w9WgXcQ", "right claimant", claimant_id="worker-01"
        )
        is True
    )


def test_enqueue_rejects_non_video_id():
    """Defensive: 11-char validation still enforced."""
    assert enqueue("not-a-real-id", retry_count=0, delay_s=0.0) is False
    assert enqueue("", retry_count=0, delay_s=0.0) is False
