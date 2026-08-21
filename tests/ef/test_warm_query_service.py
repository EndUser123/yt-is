import pytest

from ef import warm_query_service
from ef.contracts import EvidenceResult


def evidence() -> EvidenceResult:
    return EvidenceResult(
        chunk_id="video-A:transcript#00001",
        eu_id="video-A:transcript",
        video_id="video-A",
        title="A video",
        channel_id="channel-A",
        channel_title="Channel A",
        url="https://youtu.be/video-A",
        start_char=12,
        end_char=28,
        score=0.75,
        retrieval_paths=("fused", "dense"),
        snippet="rendered projection snippet",
    )


def test_serializer_preserves_thin_fields_and_authority_coordinates():
    payload = warm_query_service.serialize_result(evidence())
    assert payload["chunk_id"] == "video-A:transcript#00001"
    assert payload["eu_id"] == "video-A:transcript"
    assert payload["video_id"] == "video-A"
    assert payload["start_char"] == 12
    assert payload["end_char"] == 28
    assert payload["channel_id"] == "channel-A"
    assert payload["retrieval_paths"] == ["fused", "dense"]
    assert payload["snippet"] == "rendered projection snippet"

