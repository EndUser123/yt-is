from __future__ import annotations

import json
from pathlib import Path

import scripts.promote_excluded_categories as mod
from csf.batch_status import is_channel_blocked, upsert_channel


def _seed(db: Path) -> None:
    """Three News channels, two Education channels, one uncategorized."""
    rows = [
        ("https://www.youtube.com/channel/UCnews0000000000001", "UCnews0000000000001", "News", "News One"),
        ("https://www.youtube.com/channel/UCnews0000000000002", "UCnews0000000000002", "News", "News Two"),
        ("https://www.youtube.com/channel/UCnews0000000000003", "UCnews0000000000003", "News", "News Three"),
        ("https://www.youtube.com/channel/UCedu00000000000001", "UCedu00000000000001", "Education", "Edu One"),
        ("https://www.youtube.com/channel/UCedu00000000000002", "UCedu00000000000002", "Education", "Edu Two"),
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
    return json.loads(capsys.readouterr().out)


def test_dry_run_reports_candidates_without_blocking(tmp_path: Path, capsys) -> None:
    db = tmp_path / "batch.sqlite"
    _seed(db)
    code = mod.main(["--exclude", "News", "--db-path", str(db)])
    receipt = _receipt(capsys)
    assert code == 0
    assert receipt["mode"] == "dry-run"
    assert receipt["candidates"] == 3
    assert receipt["promoted"] == 0
    assert receipt["uncategorized_channels"] == 1
    for code_unit in ("UCnews0000000000001", "UCnews0000000000002", "UCnews0000000000003"):
        assert is_channel_blocked(f"https://www.youtube.com/channel/{code_unit}", db_path=db) is False
    assert is_channel_blocked("https://www.youtube.com/channel/UCedu00000000000001", db_path=db) is False


def test_apply_blocks_only_excluded_categories(tmp_path: Path, capsys) -> None:
    db = tmp_path / "batch.sqlite"
    _seed(db)
    code = mod.main(["--exclude", "News", "--db-path", str(db), "--apply"])
    receipt = _receipt(capsys)
    assert code == 0
    assert receipt["mode"] == "apply"
    assert receipt["promoted"] == 3
    assert receipt["already_blocked"] == 0
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000001", db_path=db) is True
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000002", db_path=db) is True
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000003", db_path=db) is True
    # Kept categories and uncategorized channels stay untouched.
    assert is_channel_blocked("https://www.youtube.com/channel/UCedu00000000000001", db_path=db) is False
    assert is_channel_blocked("https://www.youtube.com/channel/UCedu00000000000002", db_path=db) is False
    assert is_channel_blocked("https://www.youtube.com/channel/UCnew00000000000000X", db_path=db) is False


def test_apply_is_idempotent(tmp_path: Path, capsys) -> None:
    db = tmp_path / "batch.sqlite"
    _seed(db)
    assert mod.main(["--exclude", "News", "--db-path", str(db), "--apply"]) == 0
    capsys.readouterr()
    code = mod.main(["--exclude", "News", "--db-path", str(db), "--apply"])
    receipt = _receipt(capsys)
    assert code == 0
    assert receipt["promoted"] == 0
    assert receipt["already_blocked"] == 3
    assert receipt["candidates"] == 3


def test_unknown_category_fails_closed_without_mutation(tmp_path: Path, capsys) -> None:
    db = tmp_path / "batch.sqlite"
    _seed(db)
    code = mod.main(["--exclude", "Nws,News", "--db-path", str(db), "--apply"])
    assert code == 2
    err = capsys.readouterr().err
    assert "unknown categories" in err and "Nws" in err
    assert is_channel_blocked("https://www.youtube.com/channel/UCnews0000000000001", db_path=db) is False


def test_empty_exclude_list_is_rejected(tmp_path: Path, capsys) -> None:
    code = mod.main(["--exclude", " , ", "--db-path", str(tmp_path / "batch.sqlite")])
    assert code == 2


def test_receipt_path_written(tmp_path: Path, capsys) -> None:
    db = tmp_path / "batch.sqlite"
    _seed(db)
    receipt_path = tmp_path / "receipts" / "promotion.json"
    code = mod.main(
        ["--exclude", "News", "--db-path", str(db), "--apply", "--receipt-path", str(receipt_path)]
    )
    assert code == 0
    written = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert written["promoted"] == 3
