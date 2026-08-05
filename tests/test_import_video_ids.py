import json
import sys

import pytest

from csf.batch_status import BatchEntry, import_video_ids
from csf.playlist_imports import get_playlist_import_run
from scripts.import_video_ids import (
    main,
    parse_history_csv,
    parse_playlist_jsonl,
    write_decision_report,
)


def test_write_decision_report_replaces_atomically(tmp_path):
    report_path = tmp_path / "reports" / "import.json"

    write_decision_report(report_path, {"counts": {"inserted": 1}})

    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        "counts": {"inserted": 1}
    }
    assert list(report_path.parent.glob(".*.tmp")) == []

    with pytest.raises(FileExistsError):
        write_decision_report(report_path, {"counts": {"inserted": 2}})
    write_decision_report(
        report_path,
        {"counts": {"inserted": 2}},
        overwrite=True,
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["counts"]["inserted"] == 2


def test_playlist_parser_reports_rejected_records(tmp_path):
    source = tmp_path / "playlist.jsonl"
    source.write_text(
        '\n'.join([
            '{"id":"aaaaaaaaaaa","title":"ok"}',
            "not-json",
            "[]",
            '{"id":"short"}',
            '{"id":"aaaaaaaaaaa","title":"duplicate"}',
            "",
        ]) + "\n",
        encoding="utf-8",
    )

    entries, stats = parse_playlist_jsonl(source, return_stats=True)

    assert [entry.video_id for entry in entries] == ["aaaaaaaaaaa"]
    assert stats == {
        "lines_seen": 6,
        "blank_lines": 1,
        "invalid_json": 1,
        "invalid_record": 1,
        "invalid_id": 1,
        "duplicate_id": 1,
        "accepted": 1,
    }


def test_history_parser_reports_limit_and_rejections(tmp_path):
    source = tmp_path / "history.csv"
    source.write_text(
        "url,title,date\n"
        "https://example.com,omitted,01/01/2026\n"
        "https://www.youtube.com/watch?v=bbbbbbbbbbb,one,01/02/2026\n"
        "https://example.com,no,01/03/2026\n"
        "https://www.youtube.com/watch?v=bbbbbbbbbbb,dup,01/04/2026\n"
        "https://www.youtube.com/watch?v=ccccccccccc,two,01/05/2026\n",
        encoding="utf-8",
    )

    entries, stats = parse_history_csv(source, limit=4, return_stats=True)

    assert [entry.video_id for entry in entries] == ["bbbbbbbbbbb", "ccccccccccc"]
    assert stats == {
        "rows_in_file": 5,
        "rows_considered": 4,
        "rows_omitted_by_limit": 1,
        "non_youtube_row": 1,
        "duplicate_id": 1,
        "accepted": 2,
    }


def test_cli_rejects_report_path_collision(tmp_path, monkeypatch):
    playlist = tmp_path / "playlist.jsonl"
    history = tmp_path / "history.csv"
    db_path = tmp_path / "batch_status.sqlite"
    playlist.write_text("", encoding="utf-8")
    history.write_text("url,title,date\n", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_video_ids.py",
            "--playlist", str(playlist),
            "--history", str(history),
            "--db-path", str(db_path),
            "--report", str(db_path),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2


def test_cli_requires_receipt_for_execute(tmp_path, monkeypatch):
    playlist = tmp_path / "playlist.jsonl"
    history = tmp_path / "history.csv"
    playlist.write_text("", encoding="utf-8")
    history.write_text("url,title,date\n", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_video_ids.py",
            "--execute",
            "--plan", str(tmp_path / "plan.json"),
            "--playlist", str(playlist),
            "--history", str(history),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2


def test_cli_dry_run_does_not_create_provenance_db(tmp_path, monkeypatch):
    playlist = tmp_path / "playlist.jsonl"
    history = tmp_path / "history.csv"
    db_path = tmp_path / "batch_status.sqlite"
    report_path = tmp_path / "plan.json"
    provenance_path = tmp_path / "playlists.sqlite"
    playlist.write_text('{"id":"aaaaaaaaaaa","title":"one"}\n', encoding="utf-8")
    history.write_text("url,title,date\n", encoding="utf-8")
    monkeypatch.setenv("YTIS_PLAYLIST_IMPORT_DB_PATH", str(provenance_path))

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_video_ids.py",
            "--playlist", str(playlist),
            "--history", str(history),
            "--db-path", str(db_path),
            "--report", str(report_path),
        ],
    )
    main()

    assert report_path.exists()
    assert not provenance_path.exists()


def test_cli_plan_binds_execute_to_same_inputs_and_database(tmp_path, monkeypatch):
    playlist = tmp_path / "playlist.jsonl"
    history = tmp_path / "history.csv"
    db_path = tmp_path / "batch_status.sqlite"
    plan_path = tmp_path / "plan.json"
    result_path = tmp_path / "result.json"
    playlist.write_text('{"id":"aaaaaaaaaaa","title":"one"}\n', encoding="utf-8")
    history.write_text("url,title,date\n", encoding="utf-8")
    import_video_ids([], execute=True, db_path=db_path)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_video_ids.py",
            "--playlist", str(playlist),
            "--history", str(history),
            "--db-path", str(db_path),
            "--report", str(plan_path),
        ],
    )
    main()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_video_ids.py",
            "--execute",
            "--plan", str(plan_path),
            "--playlist", str(playlist),
            "--history", str(history),
            "--db-path", str(db_path),
            "--report", str(result_path),
        ],
    )
    main()

    result_report = json.loads(result_path.read_text(encoding="utf-8"))
    assert result_report["mode"] == "execute"
    assert result_report["provenance_run_id"]
    run = get_playlist_import_run(result_report["provenance_run_id"])
    assert run["status"] == "completed"
    assert json.loads(run["notes_json"])["batch_status_db_path"] == str(db_path.resolve())
    assert import_video_ids(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        db_path=db_path,
    ).counts["unchanged"] == 1


def test_cli_plan_aborts_when_database_changes(tmp_path, monkeypatch):
    playlist = tmp_path / "playlist.jsonl"
    history = tmp_path / "history.csv"
    db_path = tmp_path / "batch_status.sqlite"
    plan_path = tmp_path / "plan.json"
    result_path = tmp_path / "result.json"
    playlist.write_text('{"id":"aaaaaaaaaaa","title":"one"}\n', encoding="utf-8")
    history.write_text("url,title,date\n", encoding="utf-8")
    import_video_ids([], execute=True, db_path=db_path)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_video_ids.py",
            "--playlist", str(playlist),
            "--history", str(history),
            "--db-path", str(db_path),
            "--report", str(plan_path),
        ],
    )
    main()
    import_video_ids(
        [BatchEntry(video_id="aaaaaaaaaaa", status="complete")],
        execute=True,
        db_path=db_path,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_video_ids.py",
            "--execute",
            "--plan", str(plan_path),
            "--playlist", str(playlist),
            "--history", str(history),
            "--db-path", str(db_path),
            "--report", str(result_path),
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
