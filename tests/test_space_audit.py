from __future__ import annotations

from pathlib import Path

from csf.space_audit import (
    classify_browser_root,
    classify_run_root,
    classify_visual_file,
    build_space_audit,
    scan_visual_classes,
)


def test_classify_browser_root_marks_stale_candidates(tmp_path):
    browser_root = tmp_path / "browser" / "notebooklm-old"
    browser_root.mkdir(parents=True)
    (browser_root / "profile.dat").write_text("x", encoding="utf-8")

    row = classify_browser_root(browser_root)

    assert row.status == "candidate"
    assert row.reason == "stale browser root"


def test_classify_browser_root_keeps_active_roots(tmp_path):
    browser_root = tmp_path / "browser" / "notebooklm-pro"
    browser_root.mkdir(parents=True)

    row = classify_browser_root(browser_root)

    assert row.status == "keep"
    assert row.reason == "active benchmark browser root"


def test_classify_run_root_keeps_current_roots(tmp_path):
    run_root = tmp_path / "run29_current"
    run_root.mkdir()
    docs = tmp_path / "docs.md"
    docs.write_text(f"refs {run_root.name}", encoding="utf-8")

    row = classify_run_root(run_root, [docs])

    assert row.status == "keep"
    assert row.reason == "current benchmark root"


def test_build_space_audit_reports_candidate_run_roots(tmp_path):
    browser_root = tmp_path / "browser"
    browser_root.mkdir()
    old_browser = browser_root / "notebooklm-legacy"
    old_browser.mkdir()

    sharded_lane_root = tmp_path / "sharded_lane_series"
    sharded_lane_root.mkdir()
    run_root = sharded_lane_root / "hotel_wifi_3plus3_shared_retry_source_age_cadence_run33"
    run_root.mkdir()
    docs = tmp_path / "docs.md"
    docs.write_text(run_root.name, encoding="utf-8")

    report = build_space_audit(browser_root=browser_root, sharded_lane_root=sharded_lane_root, docs_paths=[docs])

    browser_rows = report["browser_roots"]
    run_rows = report["run_roots"]
    assert browser_rows[0]["status"] == "candidate"
    assert run_rows[0]["status"] == "candidate"
    assert run_rows[0]["reason"] == "completed and documented elsewhere"


def test_classify_visual_file_buckets(tmp_path):
    assert classify_visual_file(tmp_path / "source.mp4") == "video-source"
    assert classify_visual_file(tmp_path / "audio.mka") == "kept-audio"
    assert classify_visual_file(tmp_path / "f0001.jpg") == "frames"
    assert classify_visual_file(tmp_path / "stray.webm") == "media-unclassified"
    assert classify_visual_file(tmp_path / "note.md") == "receipts"


def test_scan_visual_classes_counts_bytes(tmp_path):
    media = tmp_path / "visual" / "vid1"
    media.mkdir(parents=True)
    (media / "audio.mka").write_bytes(b"x" * 100)
    (media / "f0001.jpg").write_bytes(b"y" * 50)
    (media / "source.mp4").write_bytes(b"z" * 25)

    classes = scan_visual_classes(tmp_path / "visual")

    assert classes["kept-audio"] == {"bytes": 100, "files": 1}
    assert classes["frames"] == {"bytes": 50, "files": 1}
    assert classes["video-source"] == {"bytes": 25, "files": 1}


def test_scan_visual_classes_missing_root(tmp_path):
    assert scan_visual_classes(tmp_path / "absent") == {}
