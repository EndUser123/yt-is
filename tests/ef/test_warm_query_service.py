import pytest
import threading
import sqlite3
from http.client import HTTPConnection
from urllib.parse import quote
from http.server import ThreadingHTTPServer

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


def test_reopen_uses_authority_not_rendered_snippet(monkeypatch):
    monkeypatch.setattr(
        warm_query_service,
        "reopen_span",
        lambda video_id, start_char, end_char: "authoritative text",
    )
    result = warm_query_service.reopen_result("video-A:transcript", 0, 18)
    assert result["text"] == "authoritative text"
    assert result["text"] != evidence().snippet


@pytest.mark.parametrize(
    ("eu_id", "start_char", "end_char"),
    [
        ("video-A:transcript", -1, 2),
        ("video-A:transcript", 4, 3),
        ("video-A:transcript", 0, warm_query_service.MAX_REOPEN_CHARS + 1),
        ("video-A:unknown", 0, 2),
        ("C:\\secret.txt", 0, 2),
    ],
)
def test_reopen_rejects_malformed_or_unsafe_coordinates(eu_id, start_char, end_char):
    with pytest.raises(ValueError):
        warm_query_service.reopen_result(eu_id, start_char, end_char)


def test_reopen_rejects_span_outside_authority(monkeypatch):
    monkeypatch.setattr(warm_query_service, "reopen_span", lambda *_: "short")
    with pytest.raises(ValueError, match="out_of_authority"):
        warm_query_service.reopen_result("video-A:transcript", 0, 10)


def test_library_lookup_reads_transcript_cache_without_writes(tmp_path, monkeypatch):
    db = tmp_path / "transcripts.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "create table transcript_cache (cache_key text, video_id text, transcript text, source text, cached_at text)"
        )
        connection.execute(
            "insert into transcript_cache values (?, ?, ?, ?, ?)",
            ("video-A:transcript", "video-A", "authoritative text", "timedtext", "2026-08-21T00:00:00Z"),
        )
    monkeypatch.setattr(warm_query_service, "TRANSCRIPTS_DB", db)
    found = warm_query_service.library_result("video-A")
    missing = warm_query_service.library_result("video-B")
    assert found["status"] == "in_library"
    assert found["eu_id"] == "video-A:transcript"
    assert found["transcript_chars"] == len("authoritative text")
    assert missing == {"video_id": "video-B", "status": "not_found"}


def test_http_reopen_endpoint_returns_authoritative_span_and_fails_closed(monkeypatch):
    monkeypatch.setattr(
        warm_query_service,
        "reopen_span",
        lambda video_id, start_char, end_char: "authoritative text",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), warm_query_service.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request(
            "GET",
            f"/reopen?eu_id={quote('video-A:transcript')}&start_char=0&end_char=18",
        )
        response = connection.getresponse()
        assert response.status == 200
        assert '"text": "authoritative text"' in response.read().decode()
        connection.close()

        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("GET", "/reopen?eu_id=C%3A%5Csecret.txt&start_char=0&end_char=2")
        response = connection.getresponse()
        assert response.status == 400
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
