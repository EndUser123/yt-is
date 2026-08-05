from __future__ import annotations

import json

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
