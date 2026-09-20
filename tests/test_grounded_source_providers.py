"""Provider propagation tests for the grounded-source IL boundary."""

from types import SimpleNamespace

from csf.providers import TranscriptProvider, VideoAnalysisResult
from ef.grounded_source import from_transcript_result


def _fetch_result():
    return SimpleNamespace(
        transcript="actual transcript text",
        video_title="Fetched title",
        raw_lang="en",
        detected_lang="en",
        lang="en",
        source="ytdlp",
        source_stage=1,
        was_translated=False,
    )


def test_transcript_provider_attaches_source_artifact(monkeypatch):
    import csf.summarize
    import csf.transcript

    fetched = _fetch_result()
    monkeypatch.setenv("YTIS_TRANSCRIPT_LANGUAGE", "fr")
    monkeypatch.setattr(
        csf.transcript,
        "fetch_transcript_chain",
        lambda _video_id, config: fetched,
    )
    monkeypatch.setattr(
        csf.summarize,
        "summarize",
        lambda **_kwargs: VideoAnalysisResult(title="LLM title", summary="short"),
    )

    result = TranscriptProvider().analyze(
        "abc12345678", "https://example.test/video"
    )

    assert result.grounded_source is not None
    assert result.grounded_source.source_text == "actual transcript text"
    assert result.grounded_source.transcript_language == "en"


def test_local_model_receives_source_text_and_propagates_artifact(monkeypatch):
    import csf.providers.lm_studio_provider as lm

    fetched = _fetch_result()
    artifact = from_transcript_result(
        "abc12345678", "https://example.test/video", fetched
    )
    transcript = VideoAnalysisResult(
        title="Fetched title",
        summary="short summary that must not be used",
        grounded_source=artifact,
    )
    monkeypatch.setattr(lm, "TranscriptProvider", lambda: SimpleNamespace(
        analyze=lambda _video_id, _video_url: transcript,
    ))
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"title":"x"}'}}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, _url, json):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(lm.httpx, "Client", FakeClient)
    result = lm.LocalModelProvider().analyze(
        "abc12345678", "https://example.test/video"
    )

    assert "actual transcript text" in captured["payload"]["messages"][1]["content"]
    assert "short summary that must not be used" not in captured["payload"]["messages"][1]["content"]
    assert result.grounded_source == artifact
