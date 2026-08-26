"""Tests for csf/video_utils.py — FFmpeg frame extraction."""

import sys
import signal
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Ensure the package is importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from csf.video_utils import extract_frames
from csf.providers import NonFatalAnalysisError


class TestExtractFrames:
    """Tests for extract_frames() FFmpeg integration."""

    def _mock_ffmpeg_result(self, returncode=0, stderr=""):
        """Return a mock subprocess.CompletedProcess."""
        mock_result = mock.Mock()
        mock_result.returncode = returncode
        mock_result.stderr = stderr
        return mock_result

    @mock.patch("subprocess.run")
    def test_extract_frames_produces_correct_count(self, mock_run):
        """Mock ffmpeg produces correct frame count from glob."""
        mock_run.return_value = self._mock_ffmpeg_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            frames_dir = Path(tmpdir)
            # Create 5 fake frame files
            for i in range(1, 6):
                (frames_dir / f"frame_{i:03d}.jpg").touch()

            with (
                mock.patch(
                    "csf.video_utils._parse_duration_ffmpeg",
                    return_value=5.0,
                ),
                mock.patch("tempfile.mkdtemp", return_value=str(frames_dir)),
            ):
                result = extract_frames(
                    frames_dir / "video.mp4", fps=1.0, max_frames=30
                )

                assert len(result) == 5

    @mock.patch("subprocess.run")
    def test_extract_frames_respects_max_frames(self, mock_run):
        """When fps * duration exceeds max_frames, result is capped."""
        mock_run.return_value = self._mock_ffmpeg_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            frames_dir = Path(tmpdir)
            # Create 50 fake frame files
            for i in range(1, 51):
                (frames_dir / f"frame_{i:03d}.jpg").touch()

            with (
                mock.patch(
                    "csf.video_utils._parse_duration_ffmpeg",
                    return_value=100.0,
                ),
                mock.patch("tempfile.mkdtemp", return_value=str(frames_dir)),
            ):
                result = extract_frames(
                    frames_dir / "video.mp4", fps=1.0, max_frames=30
                )

                # Should be capped at max_frames=30
                assert len(result) <= 30

    @mock.patch("subprocess.run")
    def test_ffmpeg_absent_raises_runtime_error(self, mock_run):
        """FileNotFoundError from subprocess.run raises RuntimeError."""
        mock_run.side_effect = FileNotFoundError("ffmpeg not found")

        with mock.patch(
            "csf.video_utils._parse_duration_ffmpeg",
            side_effect=RuntimeError("ffmpeg not found on PATH"),
        ):
            with pytest.raises(RuntimeError, match="ffmpeg not found"):
                extract_frames("/fake/video.mp4")

    @mock.patch("subprocess.run")
    def test_ffmpeg_failure_raises_nonfatal(self, mock_run):
        """Non-zero returncode from ffmpeg raises NonFatalAnalysisError."""
        mock_run.return_value = self._mock_ffmpeg_result(
            returncode=1,
            stderr="Unknown error",
        )

        with (
            mock.patch(
                "csf.video_utils._parse_duration_ffmpeg",
                return_value=10.0,
            ),
            mock.patch("tempfile.mkdtemp", return_value=tempfile.gettempdir()),
        ):
            with pytest.raises(NonFatalAnalysisError, match="return code 1"):
                extract_frames("/fake/video.mp4")

    @mock.patch("subprocess.run")
    def test_zero_frames_raises_nonfatal(self, mock_run):
        """Empty glob result (no frames) raises NonFatalAnalysisError."""
        mock_run.return_value = self._mock_ffmpeg_result()

        with (
            mock.patch(
                "csf.video_utils._parse_duration_ffmpeg",
                return_value=10.0,
            ),
            mock.patch("tempfile.mkdtemp", return_value=tempfile.gettempdir()),
        ):
            with mock.patch.object(Path, "glob", return_value=[]):
                with pytest.raises(NonFatalAnalysisError, match="0 output files"):
                    extract_frames("/fake/video.mp4")

    @mock.patch("subprocess.run")
    def test_temp_dir_preserved_on_normal_exit(self, mock_run):
        """Frames survive the call: the caller owns the output directory.

        Contract change (2026-08-18): the old implementation rmtree'd the
        temp dir in a finally block, so every consumer received paths to
        already-deleted files. Persistence is now the caller's choice via
        ``out_dir``; the default temp dir is likewise left in place.
        """
        mock_run.return_value = self._mock_ffmpeg_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            frames_dir = Path(tmpdir)
            (frames_dir / "frame_001.jpg").touch()

            with (
                mock.patch(
                    "csf.video_utils._parse_duration_ffmpeg",
                    return_value=1.0,
                ),
                mock.patch("tempfile.mkdtemp", return_value=str(frames_dir)),
            ):
                result = extract_frames(frames_dir / "video.mp4")

                assert result, "no frames returned"
                assert all(Path(f).exists() for f in result)
                assert (frames_dir / "frame_001.jpg").exists()

    @mock.patch("subprocess.run")
    def test_no_process_global_sigterm_handler(self, mock_run):
        """extract_frames must not install a process-global SIGTERM handler."""
        handler_registered = []

        def track_signal(signum, handler):
            handler_registered.append((signum, handler))

        mock_run.return_value = self._mock_ffmpeg_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            frames_dir = Path(tmpdir)
            (frames_dir / "frame_001.jpg").touch()

            with (
                mock.patch(
                    "csf.video_utils._parse_duration_ffmpeg",
                    return_value=1.0,
                ),
                mock.patch("tempfile.mkdtemp", return_value=str(frames_dir)),
                mock.patch("signal.signal", side_effect=track_signal),
            ):
                extract_frames(frames_dir / "video.mp4")

                sig_handlers = [s for s, h in handler_registered if s == signal.SIGTERM]
                assert not sig_handlers

    def test_extract_frames_invalid_fps_raises_value_error(self):
        """Non-positive fps raises ValueError."""
        with pytest.raises(ValueError, match="fps must be positive"):
            extract_frames("video.mp4", fps=0)
        with pytest.raises(ValueError, match="fps must be positive"):
            extract_frames("video.mp4", fps=-1.5)

    def test_extract_frames_invalid_max_frames_raises_value_error(self):
        """Non-positive max_frames raises ValueError."""
        with pytest.raises(ValueError, match="max_frames must be positive"):
            extract_frames("video.mp4", max_frames=0)
        with pytest.raises(ValueError, match="max_frames must be positive"):
            extract_frames("video.mp4", max_frames=-5)

    @mock.patch("subprocess.run")
    def test_temp_dir_cleaned_up_on_failure(self, mock_run, tmp_path: Path):
        """When extract_frames fails, newly created temp dir is cleaned up."""
        mock_run.return_value = self._mock_ffmpeg_result(returncode=1, stderr="fail")
        target_temp = tmp_path / "video_frames_created_temp"
        target_temp.mkdir()

        with (
            mock.patch("csf.video_utils._parse_duration_ffmpeg", return_value=1.0),
            mock.patch("tempfile.mkdtemp", return_value=str(target_temp)),
        ):
            with pytest.raises(NonFatalAnalysisError):
                extract_frames("video.mp4")
            assert not target_temp.exists()

