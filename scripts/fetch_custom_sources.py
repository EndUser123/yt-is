#!/usr/bin/env python3
"""Process custom source-labeled videos through NotebookLM batch ingest."""

import multiprocessing
import os, sys, time, sqlite3
from pathlib import Path

os.environ.setdefault("NOTEBOOKLM_PROFILE", "codex")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csf.nlm_config import get_nlm_config
from csf.nlm_batch import process_industrial_batch
from csf.batch_status import mark_complete, mark_failed
from csf.cache import has_cached_transcript, set_cached_transcript

SOURCE_LABELS = ["playlist:watch-later-temp", "history:2026-07-14"]
BATCH_SIZE = get_nlm_config().notebook_batch_size
BATCH_TIMEOUT_S = 900
DB_PATH = Path(r"P:\.data\yt-is\batch_status.sqlite")


def get_pending():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    placeholders = ",".join("?" for _ in SOURCE_LABELS)
    rows = conn.execute(
        "SELECT video_id, source FROM analysis_status "
        f"WHERE status = 'pending' AND source IN ({placeholders}) "
        "ORDER BY video_id", SOURCE_LABELS
    ).fetchall()
    conn.close()
    return rows


def batch_ids(videos, n=BATCH_SIZE):
    for i in range(0, len(videos), n):
        yield [v[0] for v in videos[i:i + n]]


def _batch_worker(vids: list[str], q: multiprocessing.Queue) -> None:
    """Worker target for multiprocessing.Process (must be module-level for spawn)."""
    try:
        result = process_industrial_batch(vids)
        q.put(result)
    except Exception as e:
        q.put(e)


def _run_with_timeout(video_ids):
    """Run process_industrial_batch with a real process-level timeout.

    Uses multiprocessing.Process so the timeout actually kills the worker
    (unlike ThreadPoolExecutor + future.cancel() which is a no-op on
    running threads).
    """
    queue: multiprocessing.Queue = multiprocessing.Queue()
    proc = multiprocessing.Process(target=_batch_worker, args=(video_ids, queue))
    proc.start()
    proc.join(timeout=BATCH_TIMEOUT_S)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        return {vid: (False, None, "timeout") for vid in video_ids}
    try:
        result = queue.get_nowait()
        if isinstance(result, Exception):
            return {vid: (False, None, str(result)) for vid in video_ids}
        return result
    except Exception:
        return {vid: (False, None, "unknown") for vid in video_ids}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    pending = get_pending()
    if not pending:
        print("No pending.")
        return

    print(f"Pending: {len(pending)}")
    for label in SOURCE_LABELS:
        cnt = sum(1 for _, s in pending if s == label)
        print(f"  {label}: {cnt}")

    batch_list = list(batch_ids(pending))
    print(f"Batches: {len(batch_list)} of {BATCH_SIZE} (timeout {BATCH_TIMEOUT_S}s)")

    if args.dry_run:
        print("DRY RUN")
        return

    total = len(pending)
    ok, fail, skip = 0, 0, 0
    started = time.monotonic()

    for idx, batch in enumerate(batch_list, 1):
        bs = time.monotonic()
        cached = [v for v in batch if has_cached_transcript(v)]
        fetch = [v for v in batch if v not in cached]
        skip += len(cached)
        for v in cached:
            mark_complete(v, source="cache", last_stage="cache")

        if fetch:
            print(f"\nBatch {idx}: {len(fetch)} fetch ({len(cached)} cached)")
            results = _run_with_timeout(fetch)
            for vid in fetch:
                ok_flag, tr, err = results.get(vid, (False, None, "unknown"))
                if ok_flag and tr:
                    # TODO: detect language from YouTube metadata instead of hardcoding "en"
                    set_cached_transcript(vid, lang="en", source="notebooklm", transcript=tr)
                    mark_complete(vid, source="notebooklm", last_stage="notebooklm")
                    ok += 1
                else:
                    mark_failed(vid, source="notebooklm", failure_reason=err or "no_transcript")
                    fail += 1

        done = ok + fail + skip
        elapsed = time.monotonic() - started
        rate = done / elapsed * 3600 if elapsed > 0 else 0
        bt = time.monotonic() - bs
        print(f"  [{idx}/{len(batch_list)}] {done}/{total} ({rate:.0f} VPH) "
              f"ok={ok} fail={fail} cached={skip} batch={bt:.1f}s")

    wall = time.monotonic() - started
    print(f"\n=== Done === ok={ok} fail={fail} cached={skip} "
          f"wall={wall:.0f}s ({ok/(wall/3600):.0f} VPH)")


if __name__ == "__main__":
    main()
