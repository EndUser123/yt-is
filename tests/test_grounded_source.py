"""Contract tests for the source-representation boundary used by IL."""

from types import SimpleNamespace

from ef import personal_graph
from ef.grounded_source import (
    GroundedSourceArtifact,
    GroundedSpan,
    bind_evidence_clusters,
    from_media_reference,
    from_transcript_text,
    from_transcript_result,
    to_inference_context,
    validate_artifact,
    with_inspected_representations,
)


def _result(text="A transcript span."):
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


def test_artifact_round_trip_and_explicit_unknown_completeness():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", _result())
    assert artifact.source_status == "unknown"
    assert artifact.transcript_language == "fr"
    assert dict(artifact.retrieval)["source_stage"] == "1"
    assert GroundedSourceArtifact.from_dict(artifact.to_dict()) == artifact
    assert bind_evidence_clusters(artifact, [3, 1, 3]).evidence_cluster_ids == (1, 3)
    assert "SOURCE_TITLE: A test video" in to_inference_context(artifact)


def test_transcript_result_cannot_be_rebound_to_requested_video():
    result = _result()
    result.video_id = "different123"
    try:
        from_transcript_result(
            "abc12345678", "https://example.test/video", result)
    except ValueError as exc:
        assert "video_id" in str(exc)
    else:
        raise AssertionError("transcript result was rebound to another video")


def test_malformed_transcript_result_identity_is_rejected():
    result = _result()
    result.video_id = 123
    try:
        from_transcript_result(
            "abc12345678", "https://example.test/video", result)
    except ValueError as exc:
        assert "non-empty string" in str(exc)
    else:
        raise AssertionError("malformed transcript result identity was accepted")


def test_plain_transcript_fallback_gets_complete_grounded_artifact():
    transcript = "prefix " + ("evidence " * 2_000) + "TAIL_MARKER"
    artifact = from_transcript_text(
        "abc12345678",
        "https://example.test/video",
        transcript,
        source="youtube_transcript_api",
    )

    assert artifact.source_text == transcript
    assert artifact.source_status == "unknown"
    assert dict(artifact.retrieval)["source"] == "youtube_transcript_api"
    validate_artifact(artifact)


def test_artifact_rejects_tampering_and_silent_truncation():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", _result())
    changed = GroundedSourceArtifact(
        source_id=artifact.source_id,
        source_url=artifact.source_url,
        title=artifact.title,
        source_status=artifact.source_status,
        representations=artifact.representations,
        transcript_language=artifact.transcript_language,
        transcript_kind=artifact.transcript_kind,
        spans=(GroundedSpan(text="changed"),),
        retrieval=artifact.retrieval,
        source_hash=artifact.source_hash,
    )
    try:
        validate_artifact(changed)
    except ValueError as exc:
        assert "hash" in str(exc)
    else:
        raise AssertionError("tampered artifact was accepted")
    try:
        to_inference_context(artifact, max_chars=20)
    except ValueError as exc:
        assert "chunk" in str(exc)
    else:
        raise AssertionError("oversized context was silently truncated")


def test_span_representation_must_be_declared_by_artifact():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", _result())
    changed = GroundedSourceArtifact(
        source_id=artifact.source_id,
        source_url=artifact.source_url,
        title=artifact.title,
        source_status=artifact.source_status,
        representations=artifact.representations,
        transcript_language=artifact.transcript_language,
        transcript_kind=artifact.transcript_kind,
        spans=(GroundedSpan(text="audio claim", representation="audio"),),
        retrieval=artifact.retrieval,
        source_hash=artifact.source_hash,
    )
    try:
        validate_artifact(changed)
    except ValueError as exc:
        assert "not declared" in str(exc)
    else:
        raise AssertionError("undeclared span representation was accepted")


def test_artifact_deserialization_rejects_coercible_malformed_fields():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", _result()).to_dict()
    artifact["evidence_cluster_ids"] = ["7"]
    try:
        GroundedSourceArtifact.from_dict(artifact)
    except ValueError as exc:
        assert "cluster ids" in str(exc)
    else:
        raise AssertionError("coercible cluster id was accepted")
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", _result()).to_dict()
    artifact["retrieval"] = [["source", 7]]
    try:
        GroundedSourceArtifact.from_dict(artifact)
    except ValueError as exc:
        assert "retrieval" in str(exc)
    else:
        raise AssertionError("malformed retrieval was accepted")


def test_cluster_binding_and_context_limit_reject_invalid_inputs():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", _result())
    try:
        bind_evidence_clusters(artifact, ["7"])
    except ValueError as exc:
        assert "cluster ids" in str(exc)
    else:
        raise AssertionError("coercible cluster id was accepted")
    try:
        to_inference_context(artifact, max_chars=0)
    except ValueError as exc:
        assert "positive integer" in str(exc)
    else:
        raise AssertionError("invalid context bound was accepted")


def test_inspected_media_is_recorded_without_inventing_textual_spans():
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", _result())
    updated = with_inspected_representations(
        artifact,
        ("frames",),
        retrieval={"frame_count": "3", "frame_extraction": "ffmpeg"},
    )
    assert updated.representations == ("transcript", "frames")
    assert updated.source_text == artifact.source_text
    assert dict(updated.retrieval)["frame_count"] == "3"
    assert updated.source_hash != artifact.source_hash


def test_media_reference_allows_provenance_without_fake_transcript_text():
    artifact = from_media_reference(
        "abc12345678",
        "https://example.test/video",
        title="Media test",
        retrieval={"transport": "youtube_url_passthrough"},
    )
    assert artifact.representations == ("full_media",)
    assert artifact.source_text == ""
    assert dict(artifact.retrieval)["transport"] == "youtube_url_passthrough"
    validate_artifact(artifact)


def test_artifact_persists_with_source_to_interest_support_edge(tmp_path):
    conn = personal_graph.connect(tmp_path / "graph.sqlite")
    try:
        artifact = bind_evidence_clusters(
            from_transcript_result(
                "abc12345678", "https://example.test/video", _result()),
            [7],
        )
        personal_graph.store_grounded_source_artifact(conn, artifact)
        row = conn.execute(
            "SELECT source_hash, evidence_cluster_ids_json "
            "FROM source_artifacts WHERE source_id=?",
            (artifact.source_id,),
        ).fetchone()
        assert row[0] == artifact.source_hash
        assert row[1] == "[7]"
    finally:
        conn.close()


def test_source_id_cannot_be_rebound_to_different_content(tmp_path):
    conn = personal_graph.connect(tmp_path / "graph.sqlite")
    try:
        first = from_transcript_result(
            "abc12345678", "https://example.test/video", _result())
        changed = from_transcript_result(
            "abc12345678", "https://example.test/video",
            _result(text="different transcript"))
        personal_graph.store_grounded_source_artifact(conn, first)
        try:
            personal_graph.store_grounded_source_artifact(conn, changed)
        except ValueError as exc:
            assert "different content" in str(exc)
        else:
            raise AssertionError("source content was silently replaced")
        row = conn.execute(
            "SELECT source_hash, spans_json FROM source_artifacts WHERE source_id=?",
            (first.source_id,),
        ).fetchone()
        assert row[0] == first.source_hash
        assert "A transcript span." in row[1]
    finally:
        conn.close()


def test_source_id_cannot_change_immutable_provenance_metadata(tmp_path):
    conn = personal_graph.connect(tmp_path / "graph.sqlite")
    try:
        first = from_transcript_result(
            "abc12345678", "https://example.test/video", _result())
        changed_url = from_transcript_result(
            "abc12345678", "https://example.test/other", _result())
        personal_graph.store_grounded_source_artifact(conn, first)
        try:
            personal_graph.store_grounded_source_artifact(conn, changed_url)
        except ValueError as exc:
            assert "immutable provenance" in str(exc)
        else:
            raise AssertionError("source provenance metadata was silently changed")
    finally:
        conn.close()
