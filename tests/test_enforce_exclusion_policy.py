"""Tests for scripts/enforce_exclusion_policy.py.

Covers the single chokepoint that the operator expects to be the source
of truth for "exclusion means blocked":

  - Adds category-reason blocks for channels in excluded categories.
  - Removes category-reason blocks whose category is no longer excluded.
  - Removes category-reason blocks for channels that became exempt (★).
  - Does not touch operator blocks (out of scope).
  - Idempotent: re-running with the same excluded_categories is a no-op.
  - Unknown categories fail closed without mutation.

Pattern: tmp_path batch DB, seed with upsert_channel. Mirrors the
existing tests/test_promote_excluded_categories.py structure.
"""

from __future__ import annotations

import json
from pathlib import Path

import scripts.enforce_exclusion_policy as mod
from csf.batch_status import block_channel, is_channel_blocked, upsert_channel


def _seed(db: Path) -> None:
    """Seed four News channels, two Education channels, one Music, one uncat."""
    rows = [
        ("https://www.youtube.com/channel/UCnews0000000000001", "UCnews0000000000001", "News", "News One"),
        ("https://www.youtube.com/channel/UCnews0000000000002", "UCnews0000000000002", "News", "News Two"),
        ("https://www.youtube.com/channel/UCnews0000000000003", "UCnews0000000000003", "News", "News Three"),
        ("https://www.youtube.com/channel/UCedu00000000000001", "UCedu00000000000001", "Education", "Edu One"),
        ("https://www.youtube.com/channel/UCedu00000000000002", "UCedu00000000000002", "Education", "Edu Two"),
        ("https://www.youtube.com/channel/UCmus00000000000001", "UCmus00000000000001", "Music", "Music One"),
        ("https://www.youtube.com/channel/UCnew00000000000000X", "UCnew00000000000000X", None, "Uncategorized One"),
    ]
    for url, channel_id, category, title in rows:
        upsert_channel(
            url,
            db_path=db,
            channel_id=channel_id,
            category=category,
            channel_title=title,
        )


def _receipt(capsys) -> dict:
    """Parse the JSON receipt from the captured output. The first line
    of the script's stdout is the only JSON document; ignore any
    trailing text (pytest's capsys sometimes appends)."""
    out = capsys.readouterr().out
    # Find the first '{' and the matching '}' (naive — works for our
    # well-formed top-level object, no nested dicts-with-whitespace).
    start = out.find("{")
    if start < 0:
        raise AssertionError(f"no JSON in output: {out!r}")
    # Walk braces to find the matching close.
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(out)):
        ch = out[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise AssertionError(f"unterminated JSON in output: {out!r}")
    return json.loads(out[start:end])


def test_enforce_adds_category_blocks_for_excluded(tmp_path: Path, capsys) -> None:
    db = tmp_path / "batch.sqlite"
    _seed(db)
    code = mod.main(["--exclude", "News,Music", "--db-path", str(db)])
    receipt = _receipt(capsys)
    assert code == 0
    assert receipt["mode"] == "apply"
    assert receipt["candidates"] == 4   # 3 News + 1 Music
    assert receipt["promoted"] == 4
    assert receipt["reconciled"] == 0
    # All four are now blocked.
    for url in (
        "https://www.youtube.com/channel/UCnews0000000000001",
        "https://www.youtube.com/channel/UCnews0000000000002",
        "https://www.youtube.com/channel/UCnews0000000000003",
        "https://www.youtube.com/channel/UCmus00000000000001",
    ):
        assert is_channel_blocked(url, db_path=db) is True
    # Education + uncategorized are not in the excluded set, not blocked.
    assert is_channel_blocked("https://www.youtube.com/channel/UCedu00000000000001", db_path=db) is False
    assert is_channel_blocked("https://www.youtube.com/channel/UCnew00000000000000X", db_path=db) is False


def test_enforce_is_idempotent(tmp_path: Path, capsys) -> None:
    db = tmp_path / "batch.sqlite"
    _seed(db)
    assert mod.main(["--exclude", "News", "--db-path", str(db)]) == 0
    capsys.readouterr()
    receipt = _receipt(
        capsys  # placeholder; replaced below
    ) if False else None
    # Second call: 0 promoted (already blocked), 0 reconciled.
    code = mod.main(["--exclude", "News", "--db-path", str(db)])
    receipt = _receipt(capsys)
    assert code == 0
    assert receipt["promoted"] == 0
    assert receipt["already_blocked"] == 3
    assert receipt["reconciled"] == 0


def test_enforce_removes_stale_category_blocks(tmp_path: Path, capsys) -> None:
    """When a category is dropped from the excluded set, blocks with
    reason='category:X' for that X must be removed. Operator blocks are
    untouched — they keep their block even when the category is un-excluded."""
    db = tmp_path / "batch.sqlite"
    _seed(db)
    # Operator manually blocked an Education channel.
    edu_op_url = "https://www.youtube.com/channel/UCedu00000000000001"
    block_channel(edu_op_url, db_path=db, reason="operator")
    # First, block all News + Education channels.
    assert mod.main(["--exclude", "News,Education", "--db-path", str(db)]) == 0
    capsys.readouterr()
    # Sanity: all News + both Education channels are blocked (operator
    # block on UCedu...01, category:Education on UCedu...02).
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000001", db_path=db)
    assert is_channel_blocked(edu_op_url, db_path=db)
    edu_cat_url = "https://www.youtube.com/channel/UCedu00000000000002"
    assert is_channel_blocked(edu_cat_url, db_path=db)
    # Now drop Education from the excluded set.
    code = mod.main(["--exclude", "News", "--db-path", str(db)])
    receipt = _receipt(capsys)
    assert code == 0
    # The category:Education block on UCedu...02 must be removed
    # (Education is no longer excluded). The operator block on
    # UCedu...01 is untouched (different reason).
    assert receipt["promoted"] == 0
    assert receipt["reconciled"] == 1
    assert edu_cat_url in receipt["reconciled_channel_urls"]
    # News blocks still in place.
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000001", db_path=db)
    # Operator block on UCedu...01 survives.
    assert is_channel_blocked(edu_op_url, db_path=db)
    # Category-reason block on UCedu...02 was removed.
    assert is_channel_blocked(edu_cat_url, db_path=db) is False


def test_enforce_removes_block_for_exempt_channel(tmp_path: Path, capsys) -> None:
    """When a channel is marked exempt (★ on the review page), its
    category-reason block must be removed. The next enforce pass is the
    one that reconciles it."""
    db = tmp_path / "batch.sqlite"
    _seed(db)
    # Block all News channels.
    assert mod.main(["--exclude", "News", "--db-path", str(db)]) == 0
    capsys.readouterr()
    exempt_url = "https://www.youtube.com/channel/UCnews0000000000001"
    assert is_channel_blocked(exempt_url, db_path=db)
    # Mark the channel as exempt (the review page's ★ path).
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE channel_metadata SET exempt_from_exclusion = 1 "
        "WHERE channel_url = ?",
        (exempt_url,),
    )
    conn.commit()
    conn.close()
    # Re-run enforce: the now-exempt channel's category-reason block
    # must be removed.
    code = mod.main(["--exclude", "News", "--db-path", str(db)])
    receipt = _receipt(capsys)
    assert code == 0
    assert receipt["reconciled"] == 1
    assert exempt_url in receipt["reconciled_channel_urls"]
    # The exempt channel is no longer blocked.
    assert is_channel_blocked(exempt_url, db_path=db) is False
    # The other News channels are still blocked.
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000002", db_path=db)
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000003", db_path=db)


def test_enforce_does_not_touch_operator_blocks(tmp_path: Path, capsys) -> None:
    """Operator blocks have reason='operator' (not 'category:X'). The
    chokepoint only owns category-reason blocks; operator blocks are
    never added or removed by it."""
    db = tmp_path / "batch.sqlite"
    _seed(db)
    edu_url = "https://www.youtube.com/channel/UCedu00000000000001"
    block_channel(edu_url, db_path=db, reason="operator")
    # Enforce with Education in the excluded set.
    assert mod.main(["--exclude", "Education", "--db-path", str(db)]) == 0
    # The operator block stays; the script tried to add a category block
    # but the channel was already blocked (operator reason), so the
    # promotion was skipped (already_blocked=1).
    assert is_channel_blocked(edu_url, db_path=db)
    # Now run with Education NOT in the excluded set. The operator
    # block must remain; nothing should change.
    code = mod.main(["--exclude", "News", "--db-path", str(db)])
    receipt = _receipt(capsys)
    assert code == 0
    assert is_channel_blocked(edu_url, db_path=db)
    assert receipt["reconciled"] == 0


def test_enforce_removes_blocks_when_category_dropped(tmp_path: Path, capsys) -> None:
    """The full add-then-remove cycle: a channel blocked under category X
    must be un-blocked when X is dropped from the excluded set."""
    db = tmp_path / "batch.sqlite"
    _seed(db)
    music_url = "https://www.youtube.com/channel/UCmus00000000000001"
    # Add Music to exclusions: Music channel gets a category-reason block.
    assert mod.main(["--exclude", "News,Music", "--db-path", str(db)]) == 0
    capsys.readouterr()
    assert is_channel_blocked(music_url, db_path=db)
    # Drop Music from exclusions: the block must be removed.
    code = mod.main(["--exclude", "News", "--db-path", str(db)])
    receipt = _receipt(capsys)
    assert code == 0
    assert music_url in receipt["reconciled_channel_urls"]
    assert is_channel_blocked(music_url, db_path=db) is False
    # News blocks stay.
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000001", db_path=db)


def test_enforce_unknown_category_fails_closed(tmp_path: Path, capsys) -> None:
    db = tmp_path / "batch.sqlite"
    _seed(db)
    code = mod.main(["--exclude", "Nws,News", "--db-path", str(db)])
    assert code == 2
    err = capsys.readouterr().err
    assert "unknown categories" in err and "Nws" in err
    # No mutation: News channels are not blocked.
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000001", db_path=db) is False


def test_enforce_empty_exclude_list_rejected(tmp_path: Path, capsys) -> None:
    code = mod.main(["--exclude", " , ", "--db-path", str(tmp_path / "batch.sqlite")])
    assert code == 2


def test_enforce_no_op_when_no_categories_given(tmp_path: Path, capsys) -> None:
    """Edge: passing no excluded categories is rejected, not silently
    no-op'd. (An empty exclusion set would mean 'exclude nothing', which
    is the default — but accepting it here would let typos pass.)"""
    db = tmp_path / "batch.sqlite"
    _seed(db)
    code = mod.main(["--exclude", "", "--db-path", str(db)])
    assert code == 2


def test_enforce_does_not_block_uncategorized_channels(tmp_path: Path, capsys) -> None:
    """Uncategorized channels (category IS NULL) must never be blocked by
    this script, even when all 11 categories are excluded. They show up
    in the receipt as 'uncategorized_channels' for visibility."""
    db = tmp_path / "batch.sqlite"
    _seed(db)
    code = mod.main(
        ["--exclude", "News,Entertainment", "--db-path", str(db)]
    )
    receipt = _receipt(capsys)
    assert code == 0
    assert receipt["uncategorized_channels"] == 1
    unc_url = "https://www.youtube.com/channel/UCnew00000000000000X"
    assert is_channel_blocked(unc_url, db_path=db) is False
