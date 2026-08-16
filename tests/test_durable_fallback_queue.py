import json

from csf.durable_fallback_queue import DurableFallbackQueue


def _queue(tmp_path):
    return DurableFallbackQueue(
        tmp_path / "nested" / "fallback.sqlite",
        queue_id="industrial-transcript-fallback",
        run_scope="run-001",
        lease_seconds=10,
    )


def _enqueue(queue, video_id="video-1"):
    return queue.enqueue(
        video_id=video_id,
        source_url=f"https://youtube.test/{video_id}",
        skip_notebooklm=True,
        failure_reason="source_add_failed",
        route_version="fallback-v1",
        now=100.0,
    )


def test_enqueue_is_idempotent_and_record_is_json_safe(tmp_path):
    with _queue(tmp_path) as queue:
        first = _enqueue(queue)
        second = _enqueue(queue)
        assert first == second
        assert json.loads(json.dumps(first, sort_keys=True)) == first
        assert first["state"] == "queued"


def test_claim_is_atomic_and_scoped(tmp_path):
    with _queue(tmp_path) as queue:
        _enqueue(queue, "video-1")
        _enqueue(queue, "video-2")
        claimed = queue.claim(claimant_id="worker-a", limit=1, now=101.0)
        assert [item["video_id"] for item in claimed] == ["video-1"]
        assert claimed[0]["state"] == "claimed"
        assert queue.claim(claimant_id="worker-b", limit=1, now=101.0)[0]["video_id"] == "video-2"


def test_stale_claim_is_reclaimed(tmp_path):
    with _queue(tmp_path) as queue:
        _enqueue(queue)
        queue.claim(claimant_id="worker-a", now=101.0)
        reclaimed = queue.claim(claimant_id="worker-b", now=112.0)
        assert reclaimed[0]["claimed_by"] == "worker-b"
        assert reclaimed[0]["attempt_count"] == 2


def test_queued_records_survive_a_fresh_queue_instance(tmp_path):
    path = tmp_path / "nested" / "fallback.sqlite"
    with DurableFallbackQueue(path, queue_id="q", run_scope="scope") as first:
        _enqueue(first)
        assert [item["video_id"] for item in first.queued()] == ["video-1"]
    with DurableFallbackQueue(path, queue_id="q", run_scope="scope") as second:
        assert [item["video_id"] for item in second.queued()] == ["video-1"]


def test_prior_claims_can_be_requeued_without_resurrecting_terminals(tmp_path):
    path = tmp_path / "nested" / "fallback.sqlite"
    with DurableFallbackQueue(path, queue_id="q", run_scope="scope") as queue:
        _enqueue(queue, "claimed")
        _enqueue(queue, "completed")
        _enqueue(queue, "failed")
        queue.claim(claimant_id="worker-a", limit=3, now=101.0)
        assert queue.complete("completed", claimant_id="worker-a", now=102.0)
        assert queue.fail("failed", claimant_id="worker-a", failure_reason="terminal", now=102.0)
        assert queue.requeue_claimed(now=103.0) == 1
        assert queue.get("claimed")["state"] == "queued"
        assert queue.get("completed")["state"] == "completed"
        assert queue.get("failed")["state"] == "failed"


def test_completion_is_idempotent_and_requires_claimant(tmp_path):
    with _queue(tmp_path) as queue:
        _enqueue(queue)
        queue.claim(claimant_id="worker-a", now=101.0)
        assert queue.complete("video-1", claimant_id="worker-b", now=102.0) is False
        assert queue.complete("video-1", claimant_id="worker-a", now=102.0) is True
        assert queue.complete("video-1", claimant_id="worker-a", now=103.0) is True


def test_failure_is_terminal_and_idempotent(tmp_path):
    with _queue(tmp_path) as queue:
        _enqueue(queue)
        queue.claim(claimant_id="worker-a", now=101.0)
        assert queue.fail("video-1", claimant_id="worker-a", failure_reason="no_transcript", now=102.0)
        assert queue.fail("video-1", claimant_id="worker-a", failure_reason="different", now=103.0)
        assert queue.get("video-1")["failure_reason"] == "no_transcript"


def test_completed_row_is_not_resurrected(tmp_path):
    with _queue(tmp_path) as queue:
        _enqueue(queue)
        queue.claim(claimant_id="worker-a", now=101.0)
        assert queue.complete("video-1", claimant_id="worker-a", now=102.0)
        late = queue.enqueue(
            video_id="video-1",
            source_url="https://youtube.test/changed",
            skip_notebooklm=False,
            failure_reason="late",
            route_version="fallback-v2",
            now=200.0,
        )
        assert late["state"] == "completed"
        assert late["source_url"] == "https://youtube.test/video-1"
        assert queue.claim(claimant_id="worker-b", now=300.0) == []


class _FailingConnProxy:
    """Delegate to the real connection but raise on SQL containing a marker."""

    def __init__(self, conn, fail_marker: str) -> None:
        self._conn = conn
        self._fail_marker = fail_marker

    def execute(self, sql, params=()):
        if self._fail_marker in sql:
            raise RuntimeError(f"simulated failure: {self._fail_marker}")
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_claim_failure_rolls_back_and_leaves_connection_usable(tmp_path, monkeypatch):
    # A claim that dies mid-transaction must not poison the shared
    # connection: without rollback the open transaction is committed by the
    # next write (partial claim) and every later BEGIN IMMEDIATE fails.
    with _queue(tmp_path) as queue:
        _enqueue(queue, "video-1")
        monkeypatch.setattr(
            queue, "_conn", _FailingConnProxy(queue._conn, "SET state='claimed'")
        )
        try:
            queue.claim(claimant_id="worker-a", now=101.0)
        except RuntimeError as exc:
            assert "SET state='claimed'" in str(exc)
        else:
            raise AssertionError("claim should re-raise the original error")
        monkeypatch.undo()

        # The row must still be queued (no partial claim committed) and the
        # connection must accept a fresh transaction.
        assert queue.get("video-1")["state"] == "queued"
        claimed = queue.claim(claimant_id="worker-b", now=102.0)
        assert [item["video_id"] for item in claimed] == ["video-1"]
        assert queue.get("video-1")["claimed_by"] == "worker-b"


def test_enqueue_failure_rolls_back_and_leaves_connection_usable(tmp_path, monkeypatch):
    with _queue(tmp_path) as queue:
        monkeypatch.setattr(
            queue, "_conn", _FailingConnProxy(queue._conn, "INSERT OR IGNORE")
        )
        try:
            _enqueue(queue, "video-1")
        except RuntimeError:
            pass
        else:
            raise AssertionError("enqueue should re-raise the original error")
        monkeypatch.undo()

        assert queue.enqueue(
            video_id="video-1",
            source_url="https://youtube.test/video-1",
            skip_notebooklm=True,
            failure_reason="source_add_failed",
            route_version="fallback-v1",
            now=100.0,
        )["state"] == "queued"
