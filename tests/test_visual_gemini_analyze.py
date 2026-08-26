"""Tests for the Stage-G Gemini analyzer: parsing, selection, agreement report."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.visual_gemini_analyze import (  # noqa: E402
    call_gemini_video,
    parse_gemini_json,
    report,
    select_for_compare,
)


def test_parse_clean_minified_json():
    verdict = parse_gemini_json(
        '{"density": 8, "summary": "Code screencast with diagrams", '
        '"code": true, "diagram": true, "chart": false, "talking_head": false}'
    )
    assert verdict == {
        "density": 8,
        "summary": "Code screencast with diagrams",
        "has_code": 1,
        "has_diagram": 1,
        "has_chart": 0,
        "talking_head": 0,
    }


def test_parse_fenced_and_prose_wrapped():
    fenced = '```json\n{"density": 3, "summary": "talking head", "code": "no",\n"diagram": false, "chart": false, "talking_head": "yes"}\n```'
    assert parse_gemini_json(fenced)["density"] == 3
    assert parse_gemini_json(fenced)["talking_head"] == 1
    prose = 'Here is my judgment: {"density": 6, "summary": "slides", "code": false, "diagram": true, "chart": false, "talking_head": false} hope it helps'
    assert parse_gemini_json(prose)["density"] == 6


def test_parse_rejects_invalid():
    assert parse_gemini_json('{"density": 0}') is None
    assert parse_gemini_json('{"density": "high"}') is None
    assert parse_gemini_json("no json at all") is None


def _mem_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE visual_vlm_scores (
               video_id TEXT PRIMARY KEY, model TEXT, density INTEGER NOT NULL,
               has_text INTEGER, has_code INTEGER, has_diagram INTEGER,
               has_chart INTEGER, has_face INTEGER, content_type TEXT,
               raw TEXT, scored_at TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE visual_gemini_scores (
               video_id TEXT PRIMARY KEY, model TEXT, density INTEGER,
               summary TEXT, has_code INTEGER, has_diagram INTEGER,
               has_chart INTEGER, talking_head INTEGER, raw TEXT,
               analyzed_at TEXT)"""
    )
    return conn


def test_select_for_compare_stratifies(tmp_path):
    conn = _mem_db()
    for i in range(10):
        conn.execute(
            "INSERT INTO visual_vlm_scores VALUES (?, 'm', ?, 1,0,0,0,0, 't', NULL, datetime('now'))",
            (f"dense{i}", 7),
        )
        conn.execute(
            "INSERT INTO visual_vlm_scores VALUES (?, 'm', ?, 1,0,0,0,0, 't', NULL, datetime('now'))",
            (f"sparse{i}", 2),
        )
    conn.execute(
        "INSERT INTO visual_gemini_scores VALUES ('dense0','m',8,'s',0,0,0,0,'',datetime('now'))"
    )
    ids = select_for_compare(conn, count=6)
    conn.close()
    assert len(ids) == 6
    assert "dense0" not in ids  # already analyzed -> excluded
    assert sum(1 for v in ids if v.startswith("dense")) == 3
    assert sum(1 for v in ids if v.startswith("sparse")) == 3


def test_report_agreement_counts(tmp_path):
    conn = _mem_db()
    rows = [
        ("a", 7, 8),   # agree dense
        ("b", 6, 3),   # mmx false positive
        ("c", 2, 7),   # mmx false negative
        ("d", 1, 2),   # agree sparse
    ]
    for vid, m, g in rows:
        conn.execute(
            "INSERT INTO visual_vlm_scores VALUES (?, 'm', ?, 1,0,0,0,0, 't', NULL, datetime('now'))",
            (vid, m),
        )
        conn.execute(
            "INSERT INTO visual_gemini_scores VALUES (?,'m',?,'s',0,0,0,0,'',datetime('now'))",
            (vid, g),
        )
    conn.commit()
    db_path = tmp_path / "t.sqlite"
    conn.commit()
    # report() opens read-only by path; persist the memory schema to file
    dest = sqlite3.connect(db_path)
    dest.executescript("".join(
        line for line in conn.iterdump() if "BEGIN" not in line and "COMMIT" not in line
    ))
    dest.commit()
    dest.close()
    conn.close()
    r = report(db_path)
    assert r["pairs"] == 4
    assert r["threshold5_agreement_pct"] == 50.0
    assert r["mmx_false_positives"] == 1
    assert r["mmx_false_negatives"] == 1
    assert r["false_negative_ids"] == ["c"]


def test_call_gemini_video_retries_503_then_succeeds(monkeypatch):
    import io
    import urllib.error

    import scripts.visual_gemini_analyze as mod

    calls = {"n": 0}

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                req.full_url, 503, "high demand", {}, io.BytesIO(b"{}")
            )
        return FakeResp(
            json.dumps(
                {"candidates": [{"content": {"parts": [{"text": '{"density": 5, "summary": "ok"}'}]}}]}
            ).encode()
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    verdict = mod.call_gemini_video("k" * 39, "abc123", timeout_s=5)
    assert calls["n"] == 2
    assert verdict["density"] == 5
