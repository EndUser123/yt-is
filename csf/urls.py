"""Shared URL utilities for yt-is.

Canonical location for YouTube URL parsing — prevents regex duplication
across scripts (import_nlm_transcripts.py, register_orphan_transcripts.py, etc.).
"""
from __future__ import annotations

import re

# Canonical YouTube video URL regex — matches all known URL formats.
# Extracts the 11-character video_id as group 1.
YT_URL_REGEX = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/v/|youtube\.com/embed/"
    r"|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})"
)


def extract_video_id(url: str) -> str | None:
    """Extract an 11-character YouTube video_id from a URL, or None."""
    m = YT_URL_REGEX.search(url or "")
    return m.group(1) if m else None
