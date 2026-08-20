"""Tests for the Stage-0 content scorer (csf/visual/content_scorer.py) and
the thumbnail store (csf/visual/thumbnails.py).

Operator-settled signal set (2026-08-18): deixis density + title/description
keywords + thumbnail CLIP probe. Words-per-second and the below-threshold
class are deliberately NOT signals (music-video ambiguity; operator veto).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from csf.visual import content_scorer as cs
from csf.visual import thumbnails as thumbs


# ---------------------------------------------------------------------------
# text scoring
# ---------------------------------------------------------------------------


def test_deixis_detected_in_screencast_transcript():
    transcript = (
        "As you can see here we have the config file. "
        "If you look at this diagram the request flows through the gateway. "
        "I've highlighted the line that fails. On the left is the editor. "
        "Let me show you the output. " * 4
    )
    result = cs.score_text(transcript, title=None)
    assert result["deixis_hits"] >= 12
    assert result["deixis_per_1000"] > 5
    assert result["text_score"] > 0.3


def test_talking_head_transcript_scores_low():
    transcript = (
        "So today I want to talk about the history of Rome. Rome was founded "
        "in 753 BC according to legend. The republic lasted centuries and the "
        "empire spread around the Mediterranean. Let me tell you about the "
        "founders and the kings and the senate. " * 6
    )
    result = cs.score_text(transcript, title="History of Rome podcast")
    assert result["deixis_hits"] == 0
    assert result["text_score"] == 0.0


def test_title_keywords_contribute():
    result = cs.score_text("no transcript", title="VS Code tutorial: build a dashboard")
    assert "tutorial" in result["title_keyword_hits"]
    assert result["text_score"] > 0


def test_music_video_signature_is_not_a_signal():
    """Words-per-second is deliberately absent: a narration-sparse music
    video must not score for sparsity alone."""
    result = cs.score_text("oh yeah", title="My Song (Official Music Video)")
    assert result["text_score"] == 0.0


def test_combined_score_thumbnail_hit_dominates():
    text = cs.score_text("plain narration text", "Some vlog")
    no_thumb = cs.combined_score(text, {"visual_hit": False})
    with_thumb = cs.combined_score(
        text, {"visual_hit": True, "visual_labels": ["code screenshot"]}
    )
    assert with_thumb["score"] > no_thumb["score"] + 0.4


def test_depth_weight_penalizes_shorts_and_rewards_tutorials():
    # Operator-approved duration prior: sub-60s shorts are the weak tail;
    # minutes-long tutorials produced the strong artifacts.
    assert cs.depth_weight(duration_s=30.0) == 0.5          # short
    assert cs.depth_weight(duration_s=120.0) == 0.8         # 2 minutes
    assert cs.depth_weight(duration_s=600.0) == 1.15        # 10-minute tutorial
    # Proxy: 1300 words / 130wpm = 10 minutes -> tutorial bonus.
    assert cs.depth_weight(transcript_words=1300) == 1.15
    # Proxy: 100 words = ~46s short.
    assert cs.depth_weight(transcript_words=100) == 0.5
    # No information at all: neutral.
    assert cs.depth_weight() == 1.0


def test_combined_score_applies_depth_weight():
    text = cs.score_text("As you can see here. " * 40, "Code tutorial demo")
    short = cs.combined_score(text, {"visual_hit": True}, duration_s=45.0)
    tutorial = cs.combined_score(text, {"visual_hit": True}, duration_s=900.0)
    assert tutorial["score"] > short["score"]
    assert short["depth_weight"] == 0.5 and tutorial["depth_weight"] == 1.15


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "batch.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    return path


def _fake_urlopen(monkeypatch, payload: bytes, content_type="image/jpeg"):
    class FakeResponse:
        def __init__(self):
            self.headers = {"Content-Type": content_type}

        def read(self, limit=-1):
            return payload[:limit] if limit > 0 else payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(thumbs.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse())


def test_fetch_thumbnail_stores_and_logs(db: Path, monkeypatch):
    _fake_urlopen(monkeypatch, b"\xff\xd8fakejpg")
    result = thumbs.fetch_thumbnail("vidThumb1", "https://i.ytimg.com/vi/vidThumb1/hq.jpg", db_path=db)
    assert result["ok"] is True and result["skipped"] is False
    assert thumbs.thumbnail_path("vidThumb1", db).exists()
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, bytes FROM media_thumbnail_log WHERE video_id='vidThumb1'").fetchone()
    conn.close()
    assert row == ("stored", len(b"\xff\xd8fakejpg"))

    # Idempotent re-fetch skips.
    again = thumbs.fetch_thumbnail("vidThumb1", "https://i.ytimg.com/vi/vidThumb1/hq.jpg", db_path=db)
    assert again["skipped"] is True


def test_fetch_thumbnail_rejects_non_image(db: Path, monkeypatch):
    _fake_urlopen(monkeypatch, b"<html>error page</html>", content_type="text/html")
    result = thumbs.fetch_thumbnail("vidBad", "https://i.ytimg.com/vi/vidBad/hq.jpg", db_path=db)
    assert result["ok"] is False
    assert thumbs.thumbnail_path("vidBad", db).exists() is False


def test_fetch_thumbnails_bounds_and_jitters(db: Path, monkeypatch):
    _fake_urlopen(monkeypatch, b"\xff\xd8jpg")
    monkeypatch.setattr(thumbs.time, "sleep", lambda s: None)
    report = thumbs.fetch_thumbnails(
        [(f"v{i}", f"https://i.ytimg.com/vi/v{i}/hq.jpg") for i in range(10)],
        db_path=db,
        max_per_run=3,
    )
    assert report["requested"] == 3
    assert report["stored"] == 3
