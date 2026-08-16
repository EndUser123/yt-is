from __future__ import annotations

import json

import pytest

from csf.batch_status import BatchEntry, import_video_ids
from csf.video_selection_manifest import load_video_selection_manifest
from scripts.build_video_selection_manifest import main


def test_builder_uses_local_db_filters_and_deterministic_order(tmp_path):
    batch_db = tmp_path / "batch_status.sqlite"
    output = tmp_path / "selection.json"
    import_video_ids(
        [
            BatchEntry(video_id="bbbbbbbbbbb", status="pending", source="source-b"),
            BatchEntry(video_id="aaaaaaaaaaa", status="pending", source="source-a"),
            BatchEntry(video_id="ccccccccccc", status="complete", source="source-a"),
            BatchEntry(video_id="ddddddddddd", status="failed", source="source-a"),
        ],
        execute=True,
        db_path=batch_db,
    )

    assert main([
        "--output", str(output),
        "--selection-name", "pending-a",
        "--db-path", str(batch_db),
        "--status", "pending",
        "--source", "source-a",
        "--order-by", "video_id",
    ]) == 0
    manifest = load_video_selection_manifest(output)
    assert [item.video_id for item in manifest.items] == ["aaaaaaaaaaa"]
    assert manifest.selection_criteria == {
        "status": "pending",
        "source": "source-a",
        "order_by": "video_id",
        "limit": None,
    }
    assert manifest.input_database_fingerprint.startswith("sha256:")

    second = tmp_path / "selection-second.json"
    main([
        "--output", str(second),
        "--selection-name", "pending-all",
        "--db-path", str(batch_db),
        "--status", "pending",
        "--order-by", "video_id",
        "--limit", "2",
    ])
    second_manifest = load_video_selection_manifest(second)
    assert [item.video_id for item in second_manifest.items] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert second_manifest.input_database_fingerprint != manifest.input_database_fingerprint
    json.loads(output.read_text(encoding="utf-8"))


def test_builder_supports_exact_ordered_video_id_file(tmp_path):
    batch_db = tmp_path / "batch_status.sqlite"
    output = tmp_path / "exact.json"
    ids_file = tmp_path / "retry_ids.txt"
    import_video_ids(
        [
            BatchEntry(video_id="aaaaaaaaaaa", status="pending", source="source-a"),
            BatchEntry(video_id="bbbbbbbbbbb", status="pending", source="source-b"),
        ],
        execute=True,
        db_path=batch_db,
    )
    ids_file.write_text("# exact retry set\nbbbbbbbbbbb\n\naaaaaaaaaaa\n", encoding="utf-8")

    assert main([
        "--output", str(output),
        "--selection-name", "exact-retry",
        "--db-path", str(batch_db),
        "--video-id-file", str(ids_file),
    ]) == 0
    manifest = load_video_selection_manifest(output)
    assert [item.video_id for item in manifest.items] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]
    assert manifest.selection_criteria["video_id_file"] == str(ids_file.resolve())


@pytest.mark.parametrize(
    ("contents", "expected_error"),
    [
        ("aaaaaaaaaaa\naaaaaaaaaaa\n", "duplicate video IDs"),
        ("missingid123\n", "missing IDs"),
    ],
)
def test_builder_rejects_invalid_exact_video_id_file(tmp_path, contents, expected_error, capsys):
    batch_db = tmp_path / "batch_status.sqlite"
    output = tmp_path / "exact.json"
    ids_file = tmp_path / "retry_ids.txt"
    import_video_ids(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        execute=True,
        db_path=batch_db,
    )
    ids_file.write_text(contents, encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        main([
            "--output", str(output),
            "--selection-name", "invalid-retry",
            "--db-path", str(batch_db),
            "--video-id-file", str(ids_file),
        ])

    assert caught.value.code == 2
    assert expected_error in capsys.readouterr().err


def test_builder_rejects_non_pending_exact_video_id_file(tmp_path, capsys):
    batch_db = tmp_path / "batch_status.sqlite"
    output = tmp_path / "exact.json"
    ids_file = tmp_path / "retry_ids.txt"
    import_video_ids(
        [BatchEntry(video_id="aaaaaaaaaaa", status="complete")],
        execute=True,
        db_path=batch_db,
    )
    ids_file.write_text("aaaaaaaaaaa\n", encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        main([
            "--output", str(output),
            "--selection-name", "stale-retry",
            "--db-path", str(batch_db),
            "--video-id-file", str(ids_file),
        ])

    assert caught.value.code == 2
    assert "filter mismatches" in capsys.readouterr().err
