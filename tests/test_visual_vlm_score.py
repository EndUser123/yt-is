"""Tests for the VLM visual intake parser and thumbnail URL handling."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.visual_vlm_score import (  # noqa: E402
    channel_priors,
    hq_thumbnail_url,
    parse_vlm_json,
    select_candidates,
)


def test_parse_clean_json_all_flags():
    verdict = parse_vlm_json(
        '{"density": 8, "text": true, "code": true, "diagram": false, '
        '"chart": true, "face": false, "type": "annotated code screencast"}'
    )
    assert verdict == {
        "density": 8,
        "has_text": 1,
        "has_code": 1,
        "has_diagram": 0,
        "has_chart": 1,
        "has_face": 0,
        "content_type": "annotated code screencast",
    }


def test_parse_prose_wrapped_json_with_string_flags():
    verdict = parse_vlm_json(
        'Sure! Here is the assessment: {"density": 2, "text": "yes", '
        '"code": "no", "diagram": false, "chart": false, "face": "true", '
        '"type": "talking head"} — hope that helps.'
    )
    assert verdict is not None
    assert verdict["density"] == 2
    assert verdict["has_text"] == 1
    assert verdict["has_code"] == 0
    assert verdict["has_face"] == 1


def test_parse_rejects_out_of_range_and_missing_density():
    assert parse_vlm_json('{"density": 0}') is None
    assert parse_vlm_json('{"density": 11}') is None
    assert parse_vlm_json('{"text": true}') is None


def test_parse_rejects_non_json():
    assert parse_vlm_json("The image shows a man at a desk.") is None
    assert parse_vlm_json("") is None


def test_hq_thumbnail_url_upgrade_and_fallback():
    assert (
        hq_thumbnail_url("abc", "https://i.ytimg.com/vi/abc/default.jpg")
        == "https://i.ytimg.com/vi/abc/hqdefault.jpg"
    )
    assert (
        hq_thumbnail_url("abc", "https://i.ytimg.com/vi/abc/maxresdefault.jpg")
        == "https://i.ytimg.com/vi/abc/maxresdefault.jpg"
    )
    assert (
        hq_thumbnail_url("abc", None)
        == "https://i.ytimg.com/vi/abc/hqdefault.jpg"
    )


# --- Prefilter: channel prior + keyword gate (bulk Path A) ---------------


def build_conn() -> sqlite3.Connection:
    """Minimal in-memory schema for the surfaces select_candidates touches.

    analysis_status carries thumbnail/title/description because the base
    query selects a.thumbnail and the prefilter matches on title/description;
    visual_vlm_scores is pared to (video_id, density) since only that pair
    feeds priors and the already-scored exclusion.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE analysis_status (
               video_id TEXT PRIMARY KEY,
               channel_id TEXT,
               status TEXT,
               updated_at TEXT,
               thumbnail TEXT,
               title TEXT,
               description TEXT
           )"""
    )
    conn.execute("CREATE TABLE video_catalog (video_id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE visual_vlm_scores (video_id TEXT PRIMARY KEY, density INTEGER)"
    )
    # The base query's not-already-queued clause references visual_jobs.
    conn.execute("CREATE TABLE visual_jobs (video_id TEXT PRIMARY KEY)")
    return conn


def fresh_ts(hours_old: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()


def add_video(conn: sqlite3.Connection, video_id: str, *, channel: str = "ch",
              hours_old: int = 1, title: str | None = None,
              description: str | None = None) -> None:
    conn.execute(
        "INSERT INTO analysis_status VALUES (?, ?, 'complete', ?, NULL, ?, ?)",
        (video_id, channel, fresh_ts(hours_old), title, description),
    )
    conn.execute("INSERT INTO video_catalog VALUES (?)", (video_id,))


def add_score(conn: sqlite3.Connection, video_id: str, density: int) -> None:
    conn.execute("INSERT INTO visual_vlm_scores VALUES (?, ?)", (video_id, density))


def test_prefilter_channel_prior_admits_dense_channel():
    conn = build_conn()
    for i, density in enumerate((5, 6, 7)):  # avg 6.0 >= 4.5 over 3 samples
        add_video(conn, f"seed{i}", channel="dense-ch", hours_old=60 + i)
        add_score(conn, f"seed{i}", density)
    add_video(conn, "backlog-new", channel="dense-ch", hours_old=10)
    assert channel_priors(conn) == {"dense-ch": 6.0}
    picked = [vid for vid, _ in select_candidates(conn, days=30, limit=10, prefilter=True)]
    assert picked == ["backlog-new"]


def test_prefilter_keyword_matches_zero_sample_channel():
    conn = build_conn()
    add_video(conn, "kw-hit", channel="cold-ch", hours_old=5,
              description="A deep dive into the wire protocol internals")
    picked = [vid for vid, _ in select_candidates(conn, days=30, limit=10, prefilter=True)]
    assert picked == ["kw-hit"]


def test_prefilter_excludes_low_prior_and_signal_free_videos():
    conn = build_conn()
    for i, density in enumerate((2, 3, 3, 4)):  # avg 3.0 < 4.5 despite 4 samples
        add_video(conn, f"sparse{i}", channel="sparse-ch", hours_old=60 + i)
        add_score(conn, f"sparse{i}", density)
    add_video(conn, "quiet-one", channel="sparse-ch", hours_old=10)
    add_video(conn, "brand-new-plain", channel="unknown-ch", hours_old=8)
    picked = [vid for vid, _ in select_candidates(conn, days=30, limit=10, prefilter=True)]
    assert picked == []


def test_no_prefilter_default_keeps_old_behavior():
    conn = build_conn()
    add_video(conn, "plain-jane", channel="any-ch", hours_old=12)  # no scores, no keywords
    picked = [vid for vid, _ in select_candidates(conn, days=30, limit=10)]
    assert picked == ["plain-jane"]

def test_prefilter_respects_cutoff_window_with_keywords():
    """Regression (run-8db0aaabe5d3 F1): with the params tuple misordered,
    cutoff landed in a LIKE slot and stale keyword videos leaked in."""
    conn = build_conn()
    add_video(conn, "fresh-kw", channel="cold-ch", hours_old=5,
              description="A deep dive into queue internals")
    add_video(conn, "stale-kw", channel="cold-ch", hours_old=24 * 90,
              description="deep dive from long ago")  # outside days=30
    picked = [vid for vid, _ in select_candidates(conn, days=30, limit=10,
                                                  prefilter=True)]
    assert "stale-kw" not in picked
    assert picked == ["fresh-kw"]


def test_score_batch_quota_stop(tmp_path):
    """MiniMax Token-Plan dry raises VLMQuotaExceeded mid-loop; score_batch
    must stop immediately with quota_stopped=True and close its connection."""
    import sqlite3

    import scripts.visual_vlm_score as v
    from scripts.visual_vlm_score import VLMQuotaExceeded

    db = tmp_path / "b.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, channel_id TEXT,
            status TEXT, updated_at TEXT, thumbnail TEXT, title TEXT,
            description TEXT);
        CREATE TABLE video_catalog (video_id TEXT PRIMARY KEY);
        CREATE TABLE visual_jobs (job_id INTEGER PRIMARY KEY, video_id TEXT UNIQUE);
        CREATE TABLE visual_vlm_scores (video_id TEXT PRIMARY KEY);
        INSERT INTO video_catalog VALUES ('q1'), ('q2');
        INSERT INTO analysis_status VALUES ('q1','c','complete','2026-08-27T00:00:00+00:00',NULL,NULL,NULL);
        INSERT INTO analysis_status VALUES ('q2','c','complete','2026-08-26T00:00:00+00:00',NULL,NULL,NULL);
        """
    )
    conn.commit()
    conn.close()

    calls = {"n": 0}

    def fake_vision(url):
        calls["n"] += 1
        raise VLMQuotaExceeded("Token Plan usage limit reached")

    orig = v.run_mmx_vision
    v.run_mmx_vision = fake_vision
    try:
        result = v.score_batch(
            db, days=30, limit=10, min_density=5, gap_s=0,
            max_consecutive_failures=5,
        )
    finally:
        v.run_mmx_vision = orig
    assert calls["n"] == 1, "quota stop must break after first dry call"
    assert result["quota_stopped"] is True and result["scored"] == 0
