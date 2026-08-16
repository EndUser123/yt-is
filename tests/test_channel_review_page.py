from __future__ import annotations

import json
from pathlib import Path

import scripts.apply_channel_review as apply_mod
import scripts.build_channel_review_page as build_mod
from csf.batch_status import _BatchStatusStorage


def _seed_channels(tmp_path: Path) -> Path:
    db = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path=db)
    conn = storage._get_conn()
    conn.executemany(
        "INSERT INTO channel_metadata (channel_url, channel_id, last_checked,"
        " channel_title, description, category) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("https://www.youtube.com/channel/UCa", "UCa", "2026-01-01", "Alpha ML", "ml", "AI/ML"),
            ("https://www.youtube.com/channel/UCb", "UCb", "2026-01-01", "Bob Personal", "", "Other"),
            ("https://www.youtube.com/channel/UCc", "UCc", "2026-01-01", "Gamma News", "news", "News"),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_build_page_embeds_data_categories_and_exclusions(tmp_path: Path):
    db = _seed_channels(tmp_path)
    out = tmp_path / "review.html"
    stats = build_mod.build_page(db, out, excluded=["News"])
    assert stats["channels"] == 3 and stats["other"] == 1
    assert stats["already_blocked"] == 0
    text = out.read_text(encoding="utf-8")
    assert '"Alpha ML"' in text and '"Bob Personal"' in text
    assert "AI/ML" in text and "Other" in text
    assert '["News"]' in text  # pre-marked excluded
    assert "a.target = '_blank'" in text and "a.rel = 'noopener'" in text  # names open in new tab
    assert "STORAGE_KEY" in text and "block_urls" in text and "localStorage" in text
    # header click = include-in-filter (set semantics); exclusions live in chips
    assert "renderChips" in text and "focusFilters.has(cat)" in text
    assert "focusFilters.delete(cat);" in text
    assert "Exclude from sync:" in text
    # sortable Channel/Subs/Videos headers with tri-state click
    assert "sortKey !== def.key" in text and "sortDir = -sortDir" in text
    assert "' ▲' : ' ▼'" in text
    # day/night theme toggle, OS-aware first load, persisted preference
    assert "body.dark" in text and "prefers-color-scheme" in text
    assert "ytis_channel_review_theme" in text
    # link + banner + excluded colors are theme-aware pairs (contrast in dark mode)
    assert "td.name a { color: var(--link); text-decoration: none; }" in text
    assert "--link: #8ab4f8" in text and "--banner-review-fg: #ffe08a" in text
    # selected cell / filtered header use theme-aware sel pair (dark: light-blue bg, dark text)
    assert "td.cell.set { background: var(--sel-bg); color: var(--sel-fg);" in text
    assert "--sel-bg: #8ab4f8; --sel-fg: #10143a;" in text
    assert "__DATA__" not in text and "__CATS__" not in text  # placeholders filled


def test_build_page_marks_already_blocked_channels(tmp_path: Path):
    db = _seed_channels(tmp_path)
    from csf.batch_status import block_channel

    block_channel("https://www.youtube.com/channel/UCc", db_path=db)
    out = tmp_path / "review.html"
    stats = build_mod.build_page(db, out, excluded=[])
    assert stats["already_blocked"] == 1
    assert '"https://www.youtube.com/channel/UCc"' in out.read_text(encoding="utf-8")


def test_apply_decisions_stores_assignments_and_updates_settings(tmp_path: Path, monkeypatch):
    db = _seed_channels(tmp_path)
    settings = tmp_path / "discovery-settings.json"
    settings.write_text(json.dumps({"excluded_categories": [], "cookies_browser": "x"}), encoding="utf-8")

    def fake_promotion(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = json.dumps({"mode": "dry-run", "candidates": 1, "promoted": 0})
            stderr = ""
        return R()

    monkeypatch.setattr(apply_mod.subprocess, "run", fake_promotion)
    decisions = {
        "assignments": {"https://www.youtube.com/channel/UCb": "Education"},
        "excluded_categories": ["News"],
        "block_urls": ["https://www.youtube.com/channel/UCa"],
    }
    receipt = apply_mod.apply_decisions(
        decisions, db_path=db, settings_path=settings, apply_promotion=False
    )
    assert receipt["assignments_applied"] == 1
    assert receipt["channels_blocked"] == 1
    assert receipt["settings_updated"] is True
    assert json.loads(settings.read_text(encoding="utf-8"))["excluded_categories"] == ["News"]
    assert receipt["promotion"]["mode"] == "dry-run"
    import sqlite3

    conn = sqlite3.connect(db)
    cat = conn.execute(
        "SELECT category FROM channel_metadata WHERE channel_url='https://www.youtube.com/channel/UCb'"
    ).fetchone()[0]
    conn.close()
    assert cat == "Education"
    conn = sqlite3.connect(db)
    source = conn.execute(
        "SELECT category_source FROM channel_metadata"
        " WHERE channel_url='https://www.youtube.com/channel/UCb'"
    ).fetchone()[0]
    conn.close()
    assert source == "manual"  # page decisions are sticky

    from csf.batch_status import is_channel_blocked

    assert is_channel_blocked("https://www.youtube.com/channel/UCa", db_path=db) is True
    assert is_channel_blocked("https://www.youtube.com/channel/UCb", db_path=db) is False


def test_apply_decisions_rejects_invalid_input(tmp_path: Path):
    db = _seed_channels(tmp_path)
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="invalid categories"):
        apply_mod.apply_decisions(
            {"assignments": {"https://x": "Banana"}}, db_path=db, settings_path=settings,
            apply_promotion=False,
        )
    with pytest.raises(ValueError, match="excluded_categories invalid"):
        apply_mod.apply_decisions(
            {"assignments": {}, "excluded_categories": ["Other"]},
            db_path=db, settings_path=settings, apply_promotion=False,
        )


def test_generated_page_script_is_valid_js(tmp_path):
    """Regression: a raw newline inside a JS string once shipped a blank page."""
    import re
    import shutil
    import subprocess

    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        local = Path.home() / "AppData/Local/Programs/nodejs/node.exe"
        node = str(local) if local.is_file() else None
    if not node:
        import pytest

        pytest.skip("node not available for JS syntax check")

    db = _seed_channels(tmp_path)
    out = tmp_path / "review.html"
    build_mod.build_page(db, out, excluded=[])
    html = out.read_text(encoding="utf-8")
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL).group(1)
    js = tmp_path / "page.js"
    js.write_text(script, encoding="utf-8")
    result = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert result.returncode == 0, f"page JS syntax error:\n{result.stderr[:400]}"


def test_archive_decisions_copies_and_deletes(tmp_path):
    import scripts.apply_channel_review as apply_mod

    export = tmp_path / "review_decisions (3).json"
    export.write_text('{"assignments": {}}', encoding="utf-8")
    dest = apply_mod.archive_decisions(export)
    assert dest is not None and dest.is_file()
    assert dest.parent.name == "applied"
    assert not export.exists()
