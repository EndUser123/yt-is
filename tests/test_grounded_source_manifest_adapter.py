"""Tests for the csf-analyze-to-bootstrap manifest adapter."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from ef.grounded_source import bind_evidence_clusters, from_transcript_result
from scripts.build_interest_graph_contract import load_grounded_source_manifest
from scripts.make_grounded_source_manifest import (
    _write_json_atomic,
    build_manifest,
)


def _artifact(video_id="video-1"):
    return from_transcript_result(
        video_id,
        f"https://youtube.example/watch?v={video_id}",
        SimpleNamespace(
            transcript="Transcript carried from the provider.",
            video_title="Provider video",
            raw_lang="en",
            detected_lang="en",
            lang="en",
            source="transcript",
            source_stage=1,
            was_translated=False,
        ),
    )


def _catalog(tmp_path):
    path = tmp_path / "catalog.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE chunk_clusters (video_id TEXT, cluster_id INTEGER)")
    conn.executemany("INSERT INTO chunk_clusters VALUES (?, ?)", [
        ("video-1", 4), ("video-1", 2), ("video-1", 4),
    ])
    conn.commit()
    conn.close()
    return path


def test_manifest_writer_publishes_complete_json_without_temp_leftovers(tmp_path):
    target = tmp_path / "grounded-sources.json"

    _write_json_atomic(target, {"manifest_version": "v1", "sources": []})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "manifest_version": "v1", "sources": []}
    assert list(tmp_path.glob(".*.tmp")) == []


def test_build_manifest_resolves_real_cluster_associations(tmp_path):
    result_path = tmp_path / "video-1.json"
    result_path.write_text(json.dumps({
        "video_id": "video-1",
        "grounded_source": _artifact().to_dict(),
    }), encoding="utf-8")

    manifest = build_manifest([result_path], catalog_path=_catalog(tmp_path))

    assert manifest["manifest_version"] == "grounded-source-manifest-v1"
    assert manifest["sources"][0]["cluster_ids"] == [2, 4]
    loaded = load_grounded_source_manifest(
        _write_manifest(tmp_path, manifest),
        eligible_cluster_ids=[2, 4],
    )
    assert sorted(loaded) == [2, 4]


def test_build_manifest_replaces_provider_cluster_ids_with_catalog_authority(tmp_path):
    result_path = tmp_path / "video-1.json"
    provider_artifact = bind_evidence_clusters(_artifact(), [999])
    result_path.write_text(json.dumps({
        "video_id": "video-1",
        "grounded_source": provider_artifact.to_dict(),
    }), encoding="utf-8")

    manifest = build_manifest([result_path], catalog_path=_catalog(tmp_path))

    assert manifest["sources"][0]["cluster_ids"] == [2, 4]
    assert manifest["sources"][0]["artifact"]["evidence_cluster_ids"] == [2, 4]


def _write_manifest(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_build_manifest_rejects_missing_source_cluster_association(tmp_path):
    result_path = tmp_path / "video-2.json"
    result_path.write_text(json.dumps({
        "video_id": "video-2",
        "grounded_source": _artifact("video-2").to_dict(),
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="no evidence-cluster association"):
        build_manifest([result_path], catalog_path=_catalog(tmp_path))


def test_build_manifest_rejects_source_id_mismatch(tmp_path):
    result_path = tmp_path / "video-1.json"
    result_path.write_text(json.dumps({
        "video_id": "different-video",
        "grounded_source": _artifact("video-1").to_dict(),
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        build_manifest([result_path], catalog_path=_catalog(tmp_path))


def test_build_manifest_rejects_repeated_source_with_different_provenance(tmp_path):
    first_path = tmp_path / "video-1-first.json"
    second_path = tmp_path / "video-1-second.json"
    first_path.write_text(json.dumps({
        "video_id": "video-1",
        "grounded_source": _artifact().to_dict(),
    }), encoding="utf-8")
    changed = _artifact().to_dict()
    changed["source_url"] = "https://youtube.example/watch?v=other"
    second_path.write_text(json.dumps({
        "video_id": "video-1",
        "grounded_source": changed,
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="immutable provenance"):
        build_manifest([first_path, second_path], catalog_path=_catalog(tmp_path))
