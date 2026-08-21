import json

from ef import ingest_extension_transcript as adapter


def request():
    return {
        "videoId": "video-12345",
        "title": "A saved video",
        "url": "https://www.youtube.com/watch?v=video-12345",
        "contextVersion": 4,
        "provider": "panel",
        "segments": [
            {"startMs": 0, "text": "First segment."},
            {"startMs": 1000, "text": "Second segment."},
        ],
    }


def test_validate_request_normalizes_transcript_and_rejects_unbound_url():
    normalized, error = adapter.validate_request(request())
    assert error is None
    assert normalized["transcript"] == "First segment.\nSecond segment."
    bad = request()
    bad["url"] = "https://example.com/video-12345"
    _, error = adapter.validate_request(bad)
    assert error == "invalid_source_url"


def test_ingest_is_idempotent_and_rejects_conflicting_existing_content(monkeypatch):
    stored = {}

    class Entry:
        def __init__(self, transcript):
            self.transcript = transcript

    monkeypatch.setattr(adapter, "get_cached_transcript_by_video_id", lambda video_id: Entry(stored[video_id]) if video_id in stored else None)
    monkeypatch.setattr(adapter, "set_cached_transcript", lambda video_id, lang, source, transcript, **kwargs: stored.setdefault(video_id, transcript))
    normalized, _ = adapter.validate_request(request())
    assert adapter.ingest(normalized)["status"] == "saved"
    assert adapter.ingest(normalized)["status"] == "already_present"
    conflicting = dict(normalized)
    conflicting["transcript"] = "different"
    assert adapter.ingest(conflicting)["status"] == "conflict"
