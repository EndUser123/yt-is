"""Offline tests for the operator-facing grounded-source manifest."""

import json
from types import SimpleNamespace

import pytest

from ef.grounded_source import from_transcript_result
from scripts.build_interest_graph_contract import load_grounded_source_manifest


def _artifact(source_id="video-1", *, text="A source-backed transcript.",
              title="A source-backed video"):
    return from_transcript_result(
        source_id,
        f"https://youtube.example/watch?v={source_id}",
        SimpleNamespace(
            transcript=text,
            video_title=title,
            raw_lang="en",
            detected_lang="en",
            lang="en",
            source="transcript",
            source_stage=1,
            was_translated=False,
        ),
    )


def _write_manifest(tmp_path, entries, version="grounded-source-manifest-v1"):
    path = tmp_path / "grounded-sources.json"
    path.write_text(json.dumps({
        "manifest_version": version,
        "sources": entries,
    }), encoding="utf-8")
    return path


def test_manifest_loads_and_merges_repeated_source_associations(tmp_path):
    artifact = _artifact()
    path = _write_manifest(tmp_path, [
        {"artifact": artifact.to_dict(), "cluster_ids": [3]},
        {"artifact": artifact.to_dict(), "cluster_ids": [1, 3]},
    ])

    loaded = load_grounded_source_manifest(path, eligible_cluster_ids=[1, 2, 3])

    assert sorted(loaded) == [1, 3]
    assert loaded[1][0].source_id == "video-1"
    assert loaded[3][0].evidence_cluster_ids == (1, 3)


def test_manifest_rejects_association_outside_bootstrap_plan(tmp_path):
    path = _write_manifest(tmp_path, [
        {"artifact": _artifact().to_dict(), "cluster_ids": [99]},
    ])

    with pytest.raises(ValueError, match="outside the eligible bootstrap plan"):
        load_grounded_source_manifest(path, eligible_cluster_ids=[1, 2])


def test_manifest_rejects_serialized_out_of_plan_association(tmp_path):
    raw_artifact = _artifact().to_dict()
    raw_artifact["evidence_cluster_ids"] = [99]
    path = _write_manifest(tmp_path, [
        {"artifact": raw_artifact, "cluster_ids": [1]},
    ])

    with pytest.raises(ValueError, match="contains artifact associations"):
        load_grounded_source_manifest(path, eligible_cluster_ids=[1, 2])


def test_manifest_entry_association_replaces_in_plan_artifact_ids(tmp_path):
    raw_artifact = _artifact().to_dict()
    raw_artifact["evidence_cluster_ids"] = [3]
    path = _write_manifest(tmp_path, [
        {"artifact": raw_artifact, "cluster_ids": [1]},
    ])

    loaded = load_grounded_source_manifest(path, eligible_cluster_ids=[1, 2, 3])

    assert loaded[1][0].evidence_cluster_ids == (1,)
    assert 3 not in loaded


def test_manifest_rejects_missing_explicit_association(tmp_path):
    path = _write_manifest(tmp_path, [
        {"artifact": _artifact().to_dict()},
    ])

    with pytest.raises(ValueError, match="requires artifact and cluster_ids"):
        load_grounded_source_manifest(path, eligible_cluster_ids=[1])


def test_manifest_rejects_conflicting_repeated_source(tmp_path):
    first = _artifact("video-1")
    second = _artifact("video-1", text="Different source content.",
                       title="Different source content")
    path = _write_manifest(tmp_path, [
        {"artifact": first.to_dict(), "cluster_ids": [1]},
        {"artifact": second.to_dict(), "cluster_ids": [2]},
    ])

    with pytest.raises(ValueError, match="conflicting content"):
        load_grounded_source_manifest(path, eligible_cluster_ids=[1, 2])


def test_manifest_rejects_repeated_source_with_different_provenance(tmp_path):
    first = _artifact()
    changed_url = _artifact().to_dict()
    changed_url["source_url"] = "https://youtube.example/watch?v=other"
    path = _write_manifest(tmp_path, [
        {"artifact": first.to_dict(), "cluster_ids": [1]},
        {"artifact": changed_url, "cluster_ids": [2]},
    ])

    with pytest.raises(ValueError, match="immutable provenance"):
        load_grounded_source_manifest(path, eligible_cluster_ids=[1, 2])
