"""Contract tests for the source-representation boundary used by IL."""

from types import SimpleNamespace
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from ef.grounded_source import (
    bind_evidence_clusters,
    GroundedSourceArtifact,
    GroundedSpan,
    from_transcript_result,
    to_inference_context,
    validate_artifact,
)
from ef import personal_graph


REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "build_interest_graph", REPO / "scripts" / "build_interest_graph.py"
)
build_interest_graph = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_interest_graph)


def transcript_result(text="A transcript span."):
    return SimpleNamespace(
        transcript=text,
        video_title="A test video",
        raw_lang="fr",
        detected_lang="fr",
        lang="fr",
        source="ytdlp",
        source_stage=1,
        was_translated=False,
    )


def test_transcript_artifact_preserves_representation_and_provenance():
    artifact = from_transcript_result(
        "abc12345678",
        "https://www.youtube.com/watch?v=abc12345678",
        transcript_result(),
    )

    assert artifact.source_status == "unknown"
    assert artifact.representations == ("transcript",)
    assert artifact.transcript_language == "fr"
    assert artifact.retrieval == (
        ("source", "ytdlp"),
        ("source_stage", "1"),
        ("translated", "False"),
    )
    assert artifact.source_text == "A transcript span."
    validate_artifact(artifact)

    round_tripped = GroundedSourceArtifact.from_dict(artifact.to_dict())
    assert round_tripped == artifact

    bound = bind_evidence_clusters(artifact, [3, 1, 3])
    assert bound.evidence_cluster_ids == (1, 3)
    assert bound.source_hash == artifact.source_hash


def test_transcript_artifact_does_not_claim_completeness():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", transcript_result()
    )
    assert artifact.source_status == "unknown"


def test_unavailable_artifact_is_explicit_and_hash_linked():
    artifact = from_transcript_result(
        "abc12345678",
        "https://example.test/video",
        transcript_result(text=""),
    )
    assert artifact.source_status == "unavailable"
    assert artifact.representations == ()
    assert artifact.spans == ()
    validate_artifact(artifact)


def test_tampering_with_source_text_fails_closed():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", transcript_result()
    )
    # Rebuilding with a changed span keeps the original hash and must fail.
    changed = replace(
        artifact,
        spans=(GroundedSpan(text="changed"),),
    )
    with pytest.raises(ValueError, match="hash"):
        validate_artifact(changed)


def test_span_representation_must_be_declared_by_artifact():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", transcript_result()
    )
    changed = replace(
        artifact,
        spans=(GroundedSpan(text="audio claim", representation="audio"),),
    )
    with pytest.raises(ValueError, match="not declared"):
        validate_artifact(changed)


def test_artifact_persists_idempotently_for_il_inputs(tmp_path):
    conn = personal_graph.connect(tmp_path / "graph.sqlite")
    try:
        artifact = from_transcript_result(
            "abc12345678", "https://example.test/video", transcript_result()
        )
        first = personal_graph.store_grounded_source_artifact(conn, artifact)
        second = personal_graph.store_grounded_source_artifact(conn, artifact)
        assert first == second
        row = conn.execute(
            "SELECT source_status, source_hash, representations_json "
            "FROM source_artifacts"
        ).fetchone()
        assert row[0] == "unknown"
        assert row[1] == artifact.source_hash
        assert row[2] == '["transcript"]'
        assert conn.execute("SELECT COUNT(*) FROM source_artifacts").fetchone()[0] == 1
    finally:
        conn.close()


def test_source_id_cannot_be_rebound_to_different_content(tmp_path):
    conn = personal_graph.connect(tmp_path / "graph.sqlite")
    try:
        first = from_transcript_result(
            "abc12345678", "https://example.test/video", transcript_result()
        )
        changed = from_transcript_result(
            "abc12345678", "https://example.test/video",
            transcript_result(text="different transcript"),
        )
        personal_graph.store_grounded_source_artifact(conn, first)
        with pytest.raises(ValueError, match="different content"):
            personal_graph.store_grounded_source_artifact(conn, changed)
        row = conn.execute(
            "SELECT source_hash, spans_json FROM source_artifacts WHERE source_id=?",
            (first.source_id,),
        ).fetchone()
        assert row[0] == first.source_hash
        assert "A transcript span." in row[1]
        extended = bind_evidence_clusters(first, [9])
        personal_graph.store_grounded_source_artifact(conn, extended)
        assert conn.execute(
            "SELECT evidence_cluster_ids_json FROM source_artifacts WHERE source_id=?",
            (first.source_id,),
        ).fetchone()[0] == "[9]"
    finally:
        conn.close()


def test_artifact_context_is_injected_into_cluster_inference_packet():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", transcript_result()
    )
    cluster = {
        "cluster_id": 7,
        "label": "test cluster",
        "terms": ["term"],
        "entities": [],
        "channels": 3,
        "documents": 4,
        "active_months": 1,
        "first_month": "2026-01",
        "last_month": "2026-01",
        "sources": [("youtube", 4)],
        "phase": None,
        "representative": [],
    }
    packet = build_interest_graph.build_packets(
        [cluster], {7: [artifact]}
    )
    assert artifact.source_hash in packet
    assert "SOURCE_STATUS: unknown" in packet
    assert "A transcript span." in packet


def test_context_refuses_silent_truncation():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", transcript_result()
    )
    with pytest.raises(ValueError, match="chunk"):
        to_inference_context(artifact, max_chars=20)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("evidence_cluster_ids", ["7"], "cluster ids"),
        ("retrieval", [["source", 7]], "retrieval"),
        ("spans", ["not an object"], "spans"),
    ],
)
def test_artifact_deserialization_rejects_coercible_malformed_fields(
    field, value, message
):
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", transcript_result()
    ).to_dict()
    artifact[field] = value
    with pytest.raises(ValueError, match=message):
        GroundedSourceArtifact.from_dict(artifact)


def test_cluster_binding_rejects_coercion_and_context_bound():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", transcript_result()
    )
    with pytest.raises(ValueError, match="cluster ids"):
        bind_evidence_clusters(artifact, ["7"])
    with pytest.raises(ValueError, match="positive integer"):
        to_inference_context(artifact, max_chars=0)


def test_grounded_source_reaches_validated_inference_and_persistence(tmp_path, monkeypatch):
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", transcript_result()
    )
    cluster = {
        "cluster_id": 7,
        "label": "test cluster",
        "terms": ["term"],
        "entities": [],
        "channels": 3,
        "documents": 4,
        "active_months": 1,
        "first_month": "2026-01",
        "last_month": "2026-01",
        "sources": [("youtube", 4)],
        "phase": None,
        "representative": [],
    }
    payload = {
        "inferred_interests": [{
            "name": "Test Interest",
            "kind": "topic",
            "parent": None,
            "temporal_state": "active",
            "stance": "learning",
            "confidence": 0.8,
            "observed_vs_inferred": "inferred",
            "goal": None,
            "information_need": None,
            "cluster_ids": [7],
            "evidence_summary": "The grounded source supports this test interest.",
            "counterevidence": None,
            "related_to": [],
        }],
        "questions": [],
        "regret_candidates": [],
    }
    monkeypatch.setattr(
        build_interest_graph,
        "_invoke_and_extract",
        lambda *_args: (payload, "test-model"),
    )

    result, meta = build_interest_graph.run_inference(
        clusters=[cluster],
        run_root=tmp_path / "run",
        grounded_sources_by_cluster={7: [artifact]},
    )
    assert result == payload
    assert meta["result_hash"]
    prompt = (tmp_path / "run" / "prompt.txt").read_text(encoding="utf-8")
    assert artifact.source_hash in prompt

    conn = personal_graph.connect(tmp_path / "graph.sqlite")
    try:
        persisted_sources = build_interest_graph._grounded_sources_for_persistence(
            (), {7: [artifact]}
        )
        assert persisted_sources[0].evidence_cluster_ids == (7,)
        bound_artifact = persisted_sources[0]
        summary = personal_graph.store_validated_inference(
            conn,
            result,
            run_id="run_grounded_source_test",
            provider="test",
            model="test-model",
            prompt_version="test-v1",
            candidate_policy="test",
            cluster_ids=[7],
            result_hash=meta["result_hash"],
            grounded_sources=[bound_artifact],
        )
        assert summary["interests"] == 1
        assert conn.execute(
            "SELECT source_hash FROM source_artifacts WHERE source_id=?",
            (bound_artifact.source_id,),
        ).fetchone()[0] == bound_artifact.source_hash
        assert conn.execute(
            "SELECT COUNT(*) FROM evidence_links "
            "WHERE relation='supports' AND src_kind='source_artifact' "
            "AND dst_kind='interest'"
        ).fetchone()[0] == 1
    finally:
        conn.close()
