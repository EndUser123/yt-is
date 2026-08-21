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

    decisions = {
        "assignments": {"https://www.youtube.com/channel/UCb": "Education"},
        "excluded_categories": ["News"],
        "block_urls": ["https://www.youtube.com/channel/UCa"],
    }
    # apply_promotion is now ignored (policy is automatic) but accepted
    # for backward compat. Pass False here to prove the parameter is
    # ignored: enforcement still runs.
    receipt = apply_mod.apply_decisions(
        decisions, db_path=db, settings_path=settings, apply_promotion=False
    )
    assert receipt["assignments_applied"] == 1
    assert receipt["channels_blocked"] == 1
    assert receipt["settings_updated"] is True
    assert json.loads(settings.read_text(encoding="utf-8"))["excluded_categories"] == ["News"]
    # NEW behavior: the chokepoint runs unconditionally. mode=apply, not
    # dry-run. The News channel (UCc) is auto-blocked via the policy
    # because its category is in excluded_categories.
    assert receipt["promotion"]["mode"] == "apply"
    assert receipt["promotion"]["excluded_categories"] == ["News"]
    assert receipt["promotion"]["candidates"] == 1
    assert receipt["promotion"]["promoted"] == 1
    assert "https://www.youtube.com/channel/UCc" in receipt["promotion"]["promoted_channel_urls"]
    # `reconciled_blocks` is set in main() (not apply_decisions), so
    # calling apply_decisions directly doesn't include it. The
    # end-to-end test below exercises main() and checks the legacy
    # field.
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

    # UCa: explicit operator block.
    assert is_channel_blocked("https://www.youtube.com/channel/UCa", db_path=db) is True
    # UCb (Education): not in excluded set, not blocked.
    assert is_channel_blocked("https://www.youtube.com/channel/UCb", db_path=db) is False
    # UCc (News): auto-blocked by the policy.
    assert is_channel_blocked("https://www.youtube.com/channel/UCc", db_path=db) is True


def test_apply_promotion_flag_is_ignored(tmp_path: Path, monkeypatch):
    """The --apply-promotion flag is now a no-op for backward compat.
    The chokepoint runs regardless. Pinning this so a future refactor
    can't silently re-introduce dry-run-by-default."""
    db = _seed_channels(tmp_path)
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    decisions = {
        "assignments": {},
        "excluded_categories": ["News"],
        "block_urls": [],
    }
    # With apply_promotion=True (was the old "actually block" path).
    r1 = apply_mod.apply_decisions(
        decisions, db_path=db, settings_path=settings, apply_promotion=True
    )
    # With apply_promotion=False (was the old "dry-run" path).
    r2 = apply_mod.apply_decisions(
        decisions, db_path=db, settings_path=settings, apply_promotion=False
    )
    # Both should be in "apply" mode and actually block UCc.
    assert r1["promotion"]["mode"] == "apply"
    assert r2["promotion"]["mode"] == "apply"
    from csf.batch_status import is_channel_blocked
    assert is_channel_blocked("https://www.youtube.com/channel/UCc", db_path=db) is True


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


# ---------------------------------------------------------------------------
# Regression: per-row X click on a viaexcluded channel must un-exclude
# (set the star exception) and NOT silently re-exclude on a second click.
# Operator-reported bug: "When I click the red X for the exclude, I want it
# to become unexcluded — in other words, included." The old handler toggled
# the exception, so a second click re-excluded the row. The new handler is
# one-way: red -> click to un-red, empty -> click to block.
# ---------------------------------------------------------------------------

def test_x_click_on_viaexcluded_row_un_excludes(tmp_path):
    """Bug: clicking X on a viaexcluded row used to TOGGLE the exception,
    so a confused second click re-excluded. Now: red X -> un-exclude, and
    a second click on the un-red row sets a per-channel block (the
    operator's intent, not a silent toggle back to the excluded state)."""
    import re

    db = _seed_channels(tmp_path)
    out = tmp_path / "review.html"
    build_mod.build_page(db, out, excluded=["News"])
    html = out.read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert m
    js = m.group(1)

    # Extract the X-cell block: from `bc.textContent` (which sets the
    # visible X) through the end of `bc.onclick` (the click handler).
    # This includes the title ladder, which lives between the text
    # assignment and the onclick definition.
    bc_start = js.find("bc.textContent = willBlock")
    assert bc_start != -1, "could not locate bc.textContent in generated JS"
    bc_end = js.find("tr.appendChild(bc);", bc_start)
    assert bc_end != -1, "could not locate end of X cell"
    x_handler = js[bc_start:bc_end]

    # The X handler must NOT contain a toggle on the exception — that's
    # the original bug. (The star handler, ec.onclick, IS supposed to
    # toggle; we don't assert on the whole page, just this handler.)
    assert "exemptions[row.u] = !isExempt(row)" not in x_handler, (
        "regression: per-row X click still toggles the star exception. "
        "Clicking X twice silently re-excludes the row the operator just "
        "freed. Use the un-red/empty-X split instead."
    )
    # The X handler must set the exception to true (one-way un-exclude)
    # when the row is currently excluded via its category.
    assert "exemptions[row.u] = true" in x_handler, (
        "regression: per-row X click no longer sets the exception. "
        "Red X on a viaexcluded row should be one-way un-exclude."
    )
    # The old misleading title must be gone from the X title ladder.
    assert "click ★ to keep this one channel" not in x_handler, (
        "regression: the X title still says 'click ★ to keep this one "
        "channel' — the X is the action, not the star."
    )
    # The new accurate title must be present in the X title ladder.
    assert "click to un-exclude this channel" in x_handler


def test_x_click_un_red_is_one_way():
    """Runtime: drive the X handler end-to-end against a known state
    with the same state shape the page uses. Confirms the click
    transitions: red X -> un-excluded, no toggle back on a second click.

    The page's onclick is a closure over `row` and module state, so we
    mirror the handler body from the source and exercise it in a Node
    sandbox. The source-level test_x_click_on_viaexcluded_row_un_excludes
    pins that this body matches what's in the shipped page.
    """
    import shutil
    import subprocess

    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        local = Path.home() / "AppData/Local/Programs/nodejs/node.exe"
        node = str(local) if local.is_file() else None
    if not node:
        import pytest
        pytest.skip("node not available for JS runtime test")

    sandbox_js = r"""
        // New X-handler logic, mirrored from the page source. The
        // source-level test_x_click_on_viaexcluded_row_un_excludes
        // pins that this body matches what's in the shipped page.
        function clickX(row, willBlock, perChannelBlocked, catExcluded) {
          if (willBlock) {
            if (perChannelBlocked) blocked[row.u] = false;
            if (catExcluded && !isExempt(row)) exemptions[row.u] = true;
          } else {
            blocked[row.u] = true;
          }
        }
        // Same state shape as the page
        const excluded = new Set(['News']);
        const blocked = {};
        const exemptions = {};
        function isExempt(row) {
          return Object.prototype.hasOwnProperty.call(exemptions, row.u)
            ? exemptions[row.u] : false;
        }
        const rowVia = { u: 'A', c: 'News' };
        const rowPlain = { u: 'B', c: 'AI/ML' };

        // Scenario 1, first click: red X on a viaexcluded row
        let wb = false || (excluded.has(rowVia.c) && !isExempt(rowVia));
        clickX(rowVia, wb, false, excluded.has(rowVia.c));
        console.log('S1_FIRST ' + JSON.stringify({
          exemptions, blocked,
          willBlock_after: false
            || (excluded.has(rowVia.c) && !isExempt(rowVia)),
        }));

        // Scenario 1, second click: X is no longer red, so willBlock
        // is false. Enters the else branch: blocked[A] = true. The
        // exception is preserved (NOT toggled off).
        wb = false || (excluded.has(rowVia.c) && !isExempt(rowVia));
        clickX(rowVia, wb, false, excluded.has(rowVia.c));
        console.log('S1_SECOND ' + JSON.stringify({ exemptions, blocked }));

        // Scenario 2: empty X on a plain row -> per-channel block
        wb = false || (excluded.has(rowPlain.c) && !isExempt(rowPlain));
        clickX(rowPlain, wb, false, excluded.has(rowPlain.c));
        console.log('S2 ' + JSON.stringify({ exemptions, blocked }));
    """
    import tempfile
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(sandbox_js)
        sandbox_path = fh.name
    try:
        run = subprocess.run(
            [node, sandbox_path], capture_output=True, text=True, timeout=30,
        )
    finally:
        Path(sandbox_path).unlink(missing_ok=True)
    assert run.returncode == 0, f"node sandbox failed: {run.stderr[:400]}"

    snapshots = {}
    for line in run.stdout.splitlines():
        for key in ("S1_FIRST", "S1_SECOND", "S2"):
            if line.startswith(key + " "):
                snapshots[key] = json.loads(line[len(key) + 1:])
    assert set(snapshots) == {"S1_FIRST", "S1_SECOND", "S2"}, (
        f"missing snapshots in stdout:\n{run.stdout}"
    )

    s1, s1b, s2 = snapshots["S1_FIRST"], snapshots["S1_SECOND"], snapshots["S2"]

    # First click: exemption set, no block yet, willBlock flips to false.
    assert s1["exemptions"] == {"A": True}
    assert s1["blocked"] == {}
    assert s1["willBlock_after"] is False

    # Second click: per-channel block set; CRUCIALLY the exception is
    # preserved. The X never silently re-excludes a row.
    assert s1b["blocked"] == {"A": True}
    assert s1b["exemptions"] == {"A": True}, (
        "REGRESSION: the second X click cleared the exception. The X "
        "must not silently re-exclude a row the operator freed."
    )

    # Empty X on a plain row adds a per-channel block.
    assert s2["blocked"] == {"A": True, "B": True}
    assert s2["exemptions"] == {"A": True}


def test_x_click_source_matches_runtime():
    """Pin the runtime test to the actual page source: the handler
    body mirrored in test_x_click_un_red_is_one_way must match the
    bc.onclick body in build_channel_review_page.py. Catches the case
    where the runtime test drifts from the shipped logic."""
    import re
    from pathlib import Path

    src = Path(
        "P:/packages/yt-is/scripts/build_channel_review_page.py"
    ).read_text(encoding="utf-8")
    m = re.search(
        r"bc\.onclick\s*=\s*\(\)\s*=>\s*\{(?P<body>.*?)\n\s*\};",
        src, re.DOTALL,
    )
    assert m, "could not locate bc.onclick in source"
    body = m.group("body")
    assert "if (willBlock)" in body, "missing willBlock branch"
    assert "blocked[row.u] = false" in body, (
        "bc.onclick no longer clears per-channel block when un-red'ing"
    )
    assert "exemptions[row.u] = true" in body, (
        "bc.onclick no longer sets the exception one-way under catExcluded"
    )
    assert "exemptions[row.u] = !isExempt(row)" not in body, (
        "regression: bc.onclick still toggles the exception"
    )
