from __future__ import annotations

from pathlib import Path

from csf.space_audit import classify_browser_root, classify_run_root, build_space_audit


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
