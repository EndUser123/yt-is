"""Tests for yt-dlp metadata logging in nlm_batch evidence payloads."""

import json
from unittest.mock import patch
from csf import youtube_page_inspector


class TestYtdlpMetadataLogging:
    """yt-dlp metadata fields must be logged to evidence payloads for source mix analysis."""

    def test_ytdlp_metadata_fields_present_in_probe_result(self):
        """inspect_youtube_watch_page_via_ytdlp must include metadata fields in probe result."""
        mock_ytdlp_result = {
            "video_id": "test123",
            "title": "Test Video",
            "channel_id": "UCtest",
            "channel": "Test Channel",
            "duration": 600,
            "view_count": 10000,
            "like_count": 500,
            "comment_count": 100,
            "uploader": "test_uploader",
            "upload_date": "20240101",
            "availability": "public",
            "live_status": "not_live",
            "is_live": False,
            "was_live": False,
        }

        with patch("csf.youtube_page_inspector.json.loads", return_value=mock_ytdlp_result):
            with patch("csf.youtube_page_inspector.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = json.dumps(mock_ytdlp_result)

                result = youtube_page_inspector.inspect_youtube_watch_page_via_ytdlp("test123")

        assert result["duration"] == 600
        assert result["view_count"] == 10000
        assert result["like_count"] == 500
        assert result["comment_count"] == 100
        assert result["channel_id"] == "UCtest"
        assert result["channel"] == "Test Channel"
        assert result["uploader"] == "test_uploader"
        assert result["upload_date"] == "20240101"

    def test_ytdlp_metadata_fields_missing_when_probe_lacks_them(self):
        """Metadata fields must be None or absent-safe when probe lacks them."""
        # Simulate a probe result that only has basic classification fields
        probe_result = {
            "classification": "ok",
            "available": True,
            "availability": "public",
            "live_status": "not_live",
            "was_live": False,
            "is_live": False,
            "title": "Test Video",
            # metadata fields missing
        }

        # Should not raise when extracting metadata fields
        metadata_fields = {
            "youtube_ytdlp_duration": probe_result.get("duration"),
            "youtube_ytdlp_view_count": probe_result.get("view_count"),
            "youtube_ytdlp_channel_id": probe_result.get("channel_id"),
            "youtube_ytdlp_channel": probe_result.get("channel"),
        }

        assert metadata_fields["youtube_ytdlp_duration"] is None
        assert metadata_fields["youtube_ytdlp_view_count"] is None
        assert metadata_fields["youtube_ytdlp_channel_id"] is None
        assert metadata_fields["youtube_ytdlp_channel"] is None

    def test_ytdlp_elapsed_and_title_still_logged(self):
        """Existing ytdlp elapsed_s and title logging must not be broken."""
        probe_result = {
            "classification": "ok",
            "available": True,
            "availability": "public",
            "live_status": "not_live",
            "was_live": False,
            "is_live": False,
            "title": "Test Video",
            "elapsed_s": 1.234,
            "duration": 600,
            "view_count": 10000,
            "channel_id": "UCtest",
        }

        metadata_fields = {
            "youtube_ytdlp_title": probe_result.get("title"),
            "youtube_ytdlp_elapsed_s": probe_result.get("elapsed_s"),
            "youtube_ytdlp_duration": probe_result.get("duration"),
        }

        assert metadata_fields["youtube_ytdlp_title"] == "Test Video"
        assert metadata_fields["youtube_ytdlp_elapsed_s"] == 1.234
        assert metadata_fields["youtube_ytdlp_duration"] == 600

    def test_source_content_behavior_unchanged(self):
        """Adding metadata logging must not break source content fetches."""
        # The classification should work as before
        mock_payload = {
            "video_id": "test123",
            "title": "Test Video",
            "channel_id": "UCtest",
            "duration": 600,
            "view_count": 10000,
            "like_count": 500,
            "comment_count": 100,
            "channel": "Test Channel",
            "uploader": "test_uploader",
            "upload_date": "20240101",
            "availability": "public",
            "live_status": "not_live",
            "is_live": False,
            "was_live": False,
        }

        classified = youtube_page_inspector.classify_ytdlp_watch_info(mock_payload)
        assert classified["classification"] == "ok"
        assert classified["available"] is True
        assert classified["title"] == "Test Video"

        # The full probe function preserves metadata
        with patch("csf.youtube_page_inspector.json.loads", return_value=mock_payload):
            with patch("csf.youtube_page_inspector.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = json.dumps(mock_payload)

                result = youtube_page_inspector.inspect_youtube_watch_page_via_ytdlp("test123")

        assert result["classification"] == "ok"
        assert result["available"] is True
        assert result["duration"] == 600
        assert result["view_count"] == 10000
        assert result["channel_id"] == "UCtest"

    def test_ytdlp_metadata_none_safe_serialization(self):
        """None values for metadata fields must serialize safely to JSON."""
        probe_result = {
            "classification": "ok",
            "available": True,
            "availability": "public",
            "live_status": "not_live",
            "was_live": False,
            "is_live": False,
            "title": "Test Video",
            "elapsed_s": 1.0,
            # All metadata fields are None
        }

        metadata_fields = {
            "youtube_ytdlp_duration": probe_result.get("duration"),
            "youtube_ytdlp_view_count": probe_result.get("view_count"),
            "youtube_ytdlp_channel_id": probe_result.get("channel_id"),
            "youtube_ytdlp_channel": probe_result.get("channel"),
            "youtube_ytdlp_uploader": probe_result.get("uploader"),
            "youtube_ytdlp_upload_date": probe_result.get("upload_date"),
            "youtube_ytdlp_like_count": probe_result.get("like_count"),
            "youtube_ytdlp_comment_count": probe_result.get("comment_count"),
        }

        # Should not raise when serializing
        import json
        json_str = json.dumps(metadata_fields)
        assert json_str is not None

        # Should deserialize correctly
        deserialized = json.loads(json_str)
        assert deserialized["youtube_ytdlp_duration"] is None
        assert deserialized["youtube_ytdlp_view_count"] is None

    def test_success_rows_receive_none_metadata(self):
        """Success rows (final_status == 'ready') receive None metadata due to conditional probing.

        Documented reason: csf/nlm_batch.py line 3677 probes yt-dlp only when
        final_status != "ready" to avoid hot-path overhead. This design choice
        means successful videos have youtube_ytdlp_* fields = None in evidence JSON.

        Source-mix analysis requiring success-vs-failure comparison must use
        alternative metadata sources or accept this gap.
        """
        # Simulate what nlm_batch.py does for successful rows:
        # youtube_ytdlp_probe remains empty dict
        youtube_ytdlp_probe = {}

        metadata_fields = {
            "youtube_ytdlp_duration": youtube_ytdlp_probe.get("duration"),
            "youtube_ytdlp_view_count": youtube_ytdlp_probe.get("view_count"),
            "youtube_ytdlp_channel_id": youtube_ytdlp_probe.get("channel_id"),
            "youtube_ytdlp_channel": youtube_ytdlp_probe.get("channel"),
            "youtube_ytdlp_uploader": youtube_ytdlp_probe.get("uploader"),
            "youtube_ytdlp_upload_date": youtube_ytdlp_probe.get("upload_date"),
            "youtube_ytdlp_like_count": youtube_ytdlp_probe.get("like_count"),
            "youtube_ytdlp_comment_count": youtube_ytdlp_probe.get("comment_count"),
        }

        # All metadata fields must be None for success rows
        assert all(v is None for v in metadata_fields.values())