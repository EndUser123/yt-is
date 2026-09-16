"""Provider propagation tests for the grounded-source IL boundary."""

from types import SimpleNamespace

import pytest

from csf.providers import (NonFatalAnalysisError, TranscriptProvider,
                           VideoAnalysisResult)
from csf.orchestrator import GeminiSDKProvider
from ef.grounded_source import from_media_reference, from_transcript_result


def test_transcript_provider_attaches_grounded_artifact(monkeypatch):
    import csf.summarize
    import csf.transcript

    fetched = SimpleNamespace(
        transcript="actual transcript text",
        video_title="Fetched title",
        raw_lang="fr",
        detected_lang="fr",
        lang="fr",
        source="ytdlp",
        source_stage=1,
        was_translated=False,
    )
    monkeypatch.setenv("YTIS_TRANSCRIPT_LANGUAGE", "fr")
    monkeypatch.setattr(
        csf.transcript, "fetch_transcript_chain",
        lambda _video_id, _config: fetched,
    )
    monkeypatch.setattr(
        csf.summarize, "summarize",
        lambda **_kwargs: VideoAnalysisResult(title="LLM title", summary="short"),
    )
    result = TranscriptProvider().analyze(
        "abc12345678", "https://example.test/video")
    assert result.grounded_source is not None
    assert result.grounded_source.source_text == "actual transcript text"
    assert result.grounded_source.title == "Fetched title"
    assert result.grounded_source.transcript_language == "fr"


def test_gemini_sdk_success_attaches_media_only_artifact(monkeypatch):
    import csf.orchestrator

    monkeypatch.setattr(
        csf.orchestrator,
        "_get_gemini_analyze",
        lambda: lambda _video_id, _video_url: {
            "title": "Full video",
            "summary": "A model-derived summary",
            "key_topics": [],
            "key_points": [],
        },
    )
    result = GeminiSDKProvider().analyze(
        "abc12345678", "https://example.test/video")
    assert result.grounded_source is not None
    assert result.grounded_source.representations == ("full_media",)
    assert result.grounded_source.source_text == ""
    assert dict(result.grounded_source.retrieval)["analysis_provider"] == "gemini_sdk"


def test_gemini_sdk_transcript_fallback_attaches_transcript_artifact(monkeypatch):
    import csf.orchestrator

    transcript = "fallback prefix " + ("evidence " * 1_200) + "TAIL_MARKER"
    monkeypatch.setattr(
        csf.orchestrator,
        "_get_gemini_analyze",
        lambda: lambda _video_id, _video_url: {
            "content": transcript,
            "fallback_reason": "video passthrough unavailable",
        },
    )
    result = GeminiSDKProvider().analyze(
        "abc12345678", "https://example.test/video")

    assert result.grounded_source is not None
    assert result.grounded_source.source_text == transcript
    assert result.grounded_source.representations == ("transcript",)


@pytest.mark.parametrize("artifact", [
    from_media_reference(
        "different-video", "https://example.test/video",
        title="stale",
    ),
    from_transcript_result(
        "abc12345678", "https://example.test/other-video",
        SimpleNamespace(transcript="text", video_title="other"),
    ),
])
def test_gemini_sdk_rejects_cross_source_grounded_artifact(monkeypatch, artifact):
    import csf.orchestrator

    monkeypatch.setattr(
        csf.orchestrator,
        "_get_gemini_analyze",
        lambda: lambda _video_id, _video_url: {
            "title": "Full video",
            "summary": "summary",
            "key_topics": [],
            "key_points": [],
            "grounded_source": artifact.to_dict(),
        },
    )

    with pytest.raises(NonFatalAnalysisError, match="grounded source"):
        GeminiSDKProvider().analyze(
            "abc12345678", "https://example.test/video")


def test_local_model_prompt_preserves_full_grounded_transcript(monkeypatch):
    import csf.providers.lm_studio_provider as lm

    transcript = "prefix " + ("evidence " * 1_200) + "TAIL_MARKER"
    fetched = VideoAnalysisResult(
        title="Source",
        grounded_source=from_transcript_result(
            "abc12345678",
            "https://example.test/video",
            SimpleNamespace(transcript=transcript, video_title="Source"),
        ),
    )
    monkeypatch.setattr(
        lm,
        "TranscriptProvider",
        lambda: SimpleNamespace(analyze=lambda *_args: fetched),
    )
    client = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    client.__enter__.return_value.post.return_value.json.return_value = {
        "choices": [{"message": {"content": '{"title":"ok"}'}}],
    }
    monkeypatch.setattr(lm.httpx, "Client", lambda **_kwargs: client)

    lm.LocalModelProvider().analyze(
        "abc12345678", "https://example.test/video")
    payload = client.__enter__.return_value.post.call_args.kwargs["json"]
    assert "TAIL_MARKER" in payload["messages"][1]["content"]
