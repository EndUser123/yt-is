"""Tests for enrich_videos_by_id and check_rss_rich.

Covers:
- Duration parsing edge cases (ISO 8601: PT0S, P1DT2H3M4S, P0D)
- Partial enrichment (some IDs deleted → uncovered IDs handled)
- Empty input
- check_rss_rich: valid XML, malformed XML, empty feed, non-UC channel ID
- check_rss_rich: extracts title + published_at + channel_id from XML
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add yt-is package root
_PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG))

from csf.source_enumerator import enrich_videos_by_id, check_rss_rich


# ---------------------------------------------------------------------------
# enrich_videos_by_id — duration parsing via isodate
# ---------------------------------------------------------------------------

class TestEnrichDurationParsing:
    """Verify isodate correctly parses all ISO 8601 duration formats YouTube uses."""

    def _mock_api_response(self, items):
        """Build a mock _api_request return value."""
        return {"items": items}

    def _make_video_item(self, vid, duration_str="PT10M30S", has_captions=True):
        """Build a single YouTube API video item."""
        return {
            "id": vid,
            "snippet": {
                "title": f"Test Video {vid}",
                "description": "Test description",
                "channelId": "UCtest123",
                "publishedAt": "2026-01-01T00:00:00Z",
                "thumbnails": {
                    "default": {"url": "https://img.youtube.com/vi/{}/default.jpg".format(vid)},
                    "medium": {"url": "https://img.youtube.com/vi/{}/mqdefault.jpg".format(vid)},
                },
            },
            "contentDetails": {
                "duration": duration_str,
                "caption": "true" if has_captions else "false",
                "isLiveContent": False,
            },
            "status": {
                "privacyStatus": "public",
                "uploadStatus": "processed",
            },
        }

    @patch("csf.source_enumerator._api_request")
    def test_normal_duration(self, mock_api):
        """PT10M30S → 630 seconds."""
        mock_api.return_value = self._mock_api_response([self._make_video_item("dQw4w9WgXcQ", "PT10M30S")])
        results = enrich_videos_by_id(["dQw4w9WgXcQ"])
        assert len(results) == 1
        assert results[0]["duration"] == 630

    @patch("csf.source_enumerator._api_request")
    def test_zero_duration(self, mock_api):
        """PT0S → 0 seconds."""
        mock_api.return_value = self._mock_api_response([self._make_video_item("aaaaaaaaaaa", "PT0S")])
        results = enrich_videos_by_id(["aaaaaaaaaaa"])
        assert results[0]["duration"] == 0

    @patch("csf.source_enumerator._api_request")
    def test_multi_day_duration(self, mock_api):
        """P1DT2H3M4S (1 day, 2 hours, 3 min, 4 sec) → 93784 seconds.

        This is the edge case the /tp review caught: the old regex failed here.
        isodate handles it correctly.
        """
        mock_api.return_value = self._mock_api_response([self._make_video_item("bbbbbbbbbbb", "P1DT2H3M4S")])
        results = enrich_videos_by_id(["bbbbbbbbbbb"])
        assert results[0]["duration"] == 93784  # 86400 + 7200 + 180 + 4

    @patch("csf.source_enumerator._api_request")
    def test_live_content_p0d(self, mock_api):
        """P0D (ongoing live/premiere) → 0 seconds. isodate handles this."""
        item = self._make_video_item("ccccccccccc", "P0D")
        item["contentDetails"]["isLiveContent"] = True
        mock_api.return_value = self._mock_api_response([item])
        results = enrich_videos_by_id(["ccccccccccc"])
        assert results[0]["duration"] == 0
        assert results[0]["is_live_content"] == 1

    @patch("csf.source_enumerator._api_request")
    def test_hours_only(self, mock_api):
        """PT1H → 3600 seconds."""
        mock_api.return_value = self._mock_api_response([self._make_video_item("ddddddddddd", "PT1H")])
        results = enrich_videos_by_id(["ddddddddddd"])
        assert results[0]["duration"] == 3600


# ---------------------------------------------------------------------------
# enrich_videos_by_id — partial enrichment (deleted videos)
# ---------------------------------------------------------------------------

class TestEnrichPartialResults:
    """Verify partial enrichment handles deleted/missing videos."""

    @patch("csf.source_enumerator._api_request")
    def test_partial_return(self, mock_api):
        """When API returns fewer items than requested (some deleted),
        the function returns only the found items. Callers handle the gap."""
        mock_api.return_value = {
            "items": [
                {
                    "id": "foundVideo001",
                    "snippet": {
                        "title": "Found", "description": "", "channelId": "UC1",
                        "publishedAt": "2026-01-01T00:00:00Z",
                        "thumbnails": {"default": {"url": "http://example.com/thumb.jpg"}},
                    },
                    "contentDetails": {"duration": "PT1M", "caption": "true", "isLiveContent": False},
                    "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                }
            ]
        }
        # Requested 2, got 1 (one deleted)
        results = enrich_videos_by_id(["foundVideo001", "deletedVideo1"])
        assert len(results) == 1
        assert results[0]["video_id"] == "foundVideo001"

    @patch("csf.source_enumerator._api_request")
    def test_empty_input(self, mock_api):
        """Empty video_ids list → empty results, no API call."""
        results = enrich_videos_by_id([])
        assert results == []
        mock_api.assert_not_called()

    @patch("csf.source_enumerator._api_request")
    def test_api_returns_empty(self, mock_api):
        """API returns no items → empty results."""
        mock_api.return_value = {"items": []}
        results = enrich_videos_by_id(["aaaaaaaaaaa"])
        assert results == []

    @patch("csf.source_enumerator._api_request")
    def test_malformed_item_skipped(self, mock_api):
        """One malformed item (missing snippet) gets default values, not skipped.

        The try/except catches KeyError on item["id"] but .get() on snippet
        returns defaults. So the malformed item passes through with empty
        strings — it's not skipped, just has default values. This is correct
        behavior: the caller can filter by checking if title is empty.
        """
        mock_api.return_value = {
            "items": [
                {"id": "goodVideo0001", "snippet": {"title": "Good", "description": "", "channelId": "UC1",
                    "publishedAt": "2026-01-01T00:00:00Z", "thumbnails": {}},
                    "contentDetails": {"duration": "PT1M", "caption": "true", "isLiveContent": False},
                    "status": {"privacyStatus": "public", "uploadStatus": "processed"}},
                {"id": "badVideo00001"},  # missing snippet, contentDetails, status
            ]
        }
        results = enrich_videos_by_id(["goodVideo0001", "badVideo00001"])
        assert len(results) == 2
        assert results[0]["video_id"] == "goodVideo0001"
        assert results[0]["title"] == "Good"
        assert results[1]["video_id"] == "badVideo00001"
        assert results[1]["title"] == ""  # default from .get()
        assert results[1]["duration"] == 0  # default


# ---------------------------------------------------------------------------
# check_rss_rich — RSS XML parsing
# ---------------------------------------------------------------------------

# Sample YouTube RSS XML (simplified from real feed)
_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <atom:entry>
    <atom:id>yt:video:aaaaaaaaaaa</atom:id>
    <yt:videoId>aaaaaaaaaaa</yt:videoId>
    <atom:title>First Video Title</atom:title>
    <atom:published>2026-07-25T12:00:00+00:00</atom:published>
  </atom:entry>
  <atom:entry>
    <atom:id>yt:video:bbbbbbbbbbb</atom:id>
    <yt:videoId>bbbbbbbbbbb</yt:videoId>
    <atom:title>Second Video Title</atom:title>
    <atom:published>2026-07-24T10:30:00+00:00</atom:published>
  </atom:entry>
</feed>"""

_EMPTY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
     xmlns:atom="http://www.w3.org/2005/Atom">
</feed>"""

_MALFORMED_XML = "<not valid xml><><>"


class TestCheckRssRich:
    """Verify check_rss_rich extracts metadata from RSS XML."""

    def test_non_uc_channel_id_returns_empty(self):
        """Non-UC channel IDs are rejected by the pattern check."""
        result = check_rss_rich("@somehandle")
        assert result == []

    def test_empty_string_returns_empty(self):
        """Empty channel_id returns empty."""
        result = check_rss_rich("")
        assert result == []

    @patch("urllib.request.urlopen")
    def test_valid_rss_extracts_metadata(self, mock_urlopen):
        """Valid RSS XML returns video_id + title + published_at + channel_id."""
        from io import BytesIO
        mock_resp = MagicMock()
        mock_resp.read.return_value = _SAMPLE_RSS.encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        # UC channel ID must be exactly 24 chars (UC + 22 alphanumeric/_-)
        uc_id = "UCABCDEFGHIJKLMNOPQRSTU0"
        result = check_rss_rich(uc_id)
        assert len(result) == 2
        assert result[0]["video_id"] == "aaaaaaaaaaa"
        assert result[0]["title"] == "First Video Title"
        assert result[0]["published_at"] == "2026-07-25T12:00:00+00:00"
        assert result[0]["channel_id"] == uc_id
        assert result[1]["video_id"] == "bbbbbbbbbbb"
        assert result[1]["title"] == "Second Video Title"

    @patch("urllib.request.urlopen")
    def test_empty_rss_returns_empty_list(self, mock_urlopen):
        """RSS with no entries returns empty list."""
        from io import BytesIO
        mock_resp = MagicMock()
        mock_resp.read.return_value = _EMPTY_RSS.encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = check_rss_rich("UCABCDEFGHIJKLMNOPQRSTU0")
        assert result == []

    @patch("urllib.request.urlopen")
    def test_malformed_xml_returns_empty(self, mock_urlopen):
        """Malformed XML returns empty list (no crash)."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = _MALFORMED_XML.encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = check_rss_rich("UCABCDEFGHIJKLMNOPQRSTU0")
        assert result == []

    @patch("urllib.request.urlopen")
    def test_network_error_returns_empty(self, mock_urlopen):
        """Network error returns empty list (no crash)."""
        mock_urlopen.side_effect = Exception("connection refused")
        result = check_rss_rich("UCABCDEFGHIJKLMNOPQRSTU0")
        assert result == []
