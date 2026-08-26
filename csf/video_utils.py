"""FFmpeg frame extraction utilities for the OCR/CLIP video analysis pipeline."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from csf.providers import NonFatalAnalysisError


def _parse_duration_ffmpeg(video_path: Path) -> float:
    """Parse video duration in seconds using ffmpeg -i.

    Extracts the Duration line from ffmpeg's stderr output and converts
    it to seconds. Returns 0.0 if the duration cannot be determined.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(video_path)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found on PATH")

    for line in result.stderr.splitlines():
        if "Duration:" not in line:
            continue
        # Format: "Duration: HH:MM:SS.ms"
        token = line.split("Duration:", 1)[1].split(",")[0].strip()
        if token:
            try:
                h, m, s = token.split(":")
                return float(h) * 3600 + float(m) * 60 + float(s)
            except (ValueError, IndexError):
                pass
    return 0.0


def extract_frames(
    video_path: str | Path,
    fps: float = 1.0,
    max_frames: int = 30,
    out_dir: str | Path | None = None,
) -> list[Path]:
    """Extract frames from a video file using FFmpeg.

    Uses uniform frame sampling at the requested FPS and returns paths to the
    extracted JPEG files. When ``out_dir`` is given, frames persist there and
    the caller owns them; otherwise a fresh temp directory is created and
    left in place for the caller (the previous implementation deleted the
    directory in a ``finally`` block before the caller could read the frames
    — every consumer received paths to deleted files).

    Args:
        video_path: Path to the input video file.
        fps: Frames per second for uniform sampling (default 1.0).
        max_frames: Maximum number of frames to extract (default 30).
        out_dir: Optional persistent output directory (created if missing).

    Returns:
        List of Path objects for the extracted frame JPEG files, sorted
        by name (i.e. chronological order).

    Raises:
        NonFatalAnalysisError: FFmpeg ran but failed (non-zero return code)
            or the output was empty — indicates a recoverable error that
            the analysis pipeline should handle gracefully.
        RuntimeError: FFmpeg is not installed or not on PATH — a truly
            unrecoverable state.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    if max_frames <= 0:
        raise ValueError(f"max_frames must be positive, got {max_frames}")

    video_path = Path(video_path)

    duration = _parse_duration_ffmpeg(video_path)
    target_count = max(int(duration * fps), 1)
    target_count = min(target_count, max_frames)

    created_temp_dir = False
    if out_dir is not None:
        temp_dir = Path(out_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix="video_frames_"))
        created_temp_dir = True

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        "-q:v",
        "2",
        str(temp_dir / "frame_%03d.jpg"),
    ]

    try:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg not found on PATH") from exc

        if result.returncode != 0:
            raise NonFatalAnalysisError(
                f"ffmpeg frame extraction failed for {video_path} "
                f"(return code {result.returncode}): {result.stderr[:500]}"
            )

        frames = sorted(temp_dir.glob("frame_*.jpg"))

        if not frames:
            raise NonFatalAnalysisError(
                f"No frames extracted for {video_path} — "
                f"ffmpeg produced 0 output files"
            )

        # Return only up to the computed target count
        return frames[:target_count]
    except BaseException:
        if (
            created_temp_dir
            and temp_dir.exists()
            and temp_dir != Path(tempfile.gettempdir())
            and temp_dir.name.startswith("video_frames_")
        ):
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)
        raise
