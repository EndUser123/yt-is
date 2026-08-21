"""Bounded stdin adapter for saving one browser-acquired YouTube transcript.

This is intentionally a fixed module rather than a general command runner. The
native companion supplies one validated JSON object, and this module writes
through the existing ``csf.cache.set_cached_transcript`` authority.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from csf.cache import get_cached_transcript_by_video_id, set_cached_transcript

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_SEGMENTS = 4096
MAX_SEGMENT_BYTES = 32 * 1024
MAX_TRANSCRIPT_BYTES = 2_000_000


def validate_request(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict):
        return None, "invalid_request"
    video_id = value.get("videoId")
    title = value.get("title")
    url = value.get("url")
    segments = value.get("segments")
    if (
        not isinstance(video_id, str)
        or not VIDEO_ID_RE.fullmatch(video_id)
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 512
        or not isinstance(url, str)
        or len(url) > 2048
        or not isinstance(segments, list)
        or not segments
        or len(segments) > MAX_SEGMENTS
    ):
        return None, "invalid_request"
    if not (url.startswith(f"https://www.youtube.com/watch?v={video_id}") or url.startswith(f"https://youtube.com/watch?v={video_id}") or url.startswith(f"https://youtu.be/{video_id}")):
        return None, "invalid_source_url"
    normalized: list[dict[str, Any]] = []
    previous_start = -1
    for segment in segments:
        if not isinstance(segment, dict):
            return None, "invalid_segments"
        start_ms = segment.get("startMs")
        text = segment.get("text")
        if (
            not isinstance(start_ms, int)
            or isinstance(start_ms, bool)
            or start_ms < 0
            or start_ms < previous_start
            or not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > MAX_SEGMENT_BYTES
        ):
            return None, "invalid_segments"
        previous_start = start_ms
        normalized.append({"startMs": start_ms, "text": text})
    transcript = "\n".join(item["text"] for item in normalized).strip()
    if not transcript or len(transcript.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        return None, "transcript_too_large"
    return {
        "videoId": video_id,
        "title": title.strip(),
        "url": url,
        "segments": normalized,
        "transcript": transcript,
        "lang": value.get("lang") if isinstance(value.get("lang"), str) and value.get("lang") else "en",
        "provider": value.get("provider") if isinstance(value.get("provider"), str) else "browser",
        "contextVersion": value.get("contextVersion") if isinstance(value.get("contextVersion"), int) else 0,
    }, None


def ingest(request: dict[str, Any]) -> dict[str, Any]:
    video_id = request["videoId"]
    existing = get_cached_transcript_by_video_id(video_id)
    if existing is not None:
        if existing.transcript != request["transcript"]:
            return {"status": "conflict", "videoId": video_id, "error": "existing_transcript_differs"}
        return {"status": "already_present", "videoId": video_id, "transcriptChars": len(existing.transcript)}
    metadata = {
        "title": request["title"],
        "url": request["url"],
        "provider": request["provider"],
        "context_version": request["contextVersion"],
        "segment_count": len(request["segments"]),
    }
    set_cached_transcript(
        video_id,
        request["lang"],
        "yt-workspace",
        request["transcript"],
        metadata=metadata,
        bind_verified=True,
    )
    stored = get_cached_transcript_by_video_id(video_id)
    if stored is None or stored.transcript != request["transcript"]:
        return {"status": "write_failed", "videoId": video_id, "error": "cache_write_not_observed"}
    return {"status": "saved", "videoId": video_id, "transcriptChars": len(stored.transcript)}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        print(json.dumps({"status": "rejected", "error": "malformed_json"}))
        return 2
    request, error = validate_request(payload)
    if error or request is None:
        print(json.dumps({"status": "rejected", "error": error or "invalid_request"}))
        return 2
    print(json.dumps(ingest(request), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
