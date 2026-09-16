"""OCR + CLIP provider (Tier 2) for zero-cost video analysis.

Flow: YouTube transcript fetch → FFmpeg frame extraction → EasyOCR code capture → CLIP tagging → LLM summarization.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from csf.providers import NonFatalAnalysisError, VideoAnalysisResult
from ef.grounded_source import (
    from_transcript_result,
    with_inspected_representations,
)
from csf.video_utils import (
    extract_frames,
    NonFatalAnalysisError as VideoUtilsNonFatalAnalysisError,
)
from csf.ocr_client import extract_code_snippets
from csf.clip_client import tag_frames
from csf.summarize import summarize


class OcrClipProvider:
    """Tier 2: OCR + CLIP pipeline (zero API cost, captures code-on-screen + visual tags)."""

    __slots__ = ()

    def analyze(self, video_id: str, video_url: str) -> VideoAnalysisResult:
        """Analyze a video using OCR (code screens) + CLIP (visual tags) + LLM summarization.

        Flow:
          1. Fetch YouTube transcript
          2. Download/extract frames via FFmpeg
          3. Run EasyOCR to capture on-screen code
          4. Run CLIP to tag visual content
          5. Run LLM summarization combining all signals

        Args:
            video_id: YouTube video ID.
            video_url: Full YouTube URL.

        Returns:
            VideoAnalysisResult with code_snippets and visual_tags populated.

        Raises:
            NonFatalAnalysisError: FFmpeg absent/fails, transcript unavailable, or any
                unexpected error — orchestrator will fall back to next tier.
        """
        # ---- Step 1: Fetch transcript ----
        try:
            from csf.transcript import fetch_transcript_chain, LanguageConfig

            transcript_result = fetch_transcript_chain(
                video_id,
                LanguageConfig(
                    prefer_lang=os.environ.get("YTIS_TRANSCRIPT_LANGUAGE", "en"),
                ),
            )
            transcript_text = transcript_result.transcript
        except Exception as e:
            raise NonFatalAnalysisError(
                f"OcrClipProvider: transcript fetch failed for {video_id}: {e}"
            )

        if not transcript_text:
            raise NonFatalAnalysisError(
                f"OcrClipProvider: no transcript available for {video_id}"
            )

        grounded_source = from_transcript_result(
            video_id, video_url, transcript_result)

        # ---- Step 2: Extract frames via FFmpeg ----
        frames: list[Path] = []
        try:
            frames = extract_frames(video_url, fps=1.0, max_frames=30)
        except VideoUtilsNonFatalAnalysisError:
            # FFmpeg absent or failed — non-fatal, fall back to transcript-only
            raise
        except RuntimeError as e:
            # Truly unrecoverable (ffmpeg not installed at all)
            raise NonFatalAnalysisError(f"OcrClipProvider: FFmpeg unavailable: {e}")
        except Exception as e:
            raise NonFatalAnalysisError(
                f"OcrClipProvider: frame extraction failed for {video_id}: {e}"
            )

        if not frames:
            # No frames extracted — proceed with empty code_snippets/visual_tags
            return replace(
                summarize(
                    transcript=transcript_text,
                    code_snippets=[],
                    visual_tags=[],
                ),
                grounded_source=grounded_source,
            )

        grounded_source = with_inspected_representations(
            grounded_source,
            ("frames",),
            retrieval={
                "frame_extraction": "ffmpeg",
                "frame_count": str(len(frames)),
            },
        )

        # ---- Step 3: EasyOCR — capture on-screen code ----
        code_snippets: list[str] = []
        try:
            code_snippets = extract_code_snippets(frames, timeout_per_image=30.0)
        except Exception:
            # OCR failure is non-fatal — continue with empty code_snippets
            pass

        # ---- Step 4: CLIP — tag visual content ----
        visual_tags: list[str] = []
        try:
            visual_tags = tag_frames(frames, timeout_per_image=30.0)
        except Exception:
            # CLIP failure is non-fatal — continue with empty visual_tags
            pass

        # ---- Step 5: LLM summarization ----
        try:
            return replace(
                summarize(
                    transcript=transcript_text,
                    code_snippets=code_snippets,
                    visual_tags=visual_tags,
                ),
                grounded_source=grounded_source,
            )
        except Exception as e:
            # Summarization failure — return partial result rather than failing entirely
            return VideoAnalysisResult(
                title="Unknown",
                summary="",
                key_topics=[],
                key_points=[],
                code_snippets=code_snippets,
                visual_tags=visual_tags,
                mode="ocr_clip",
                fallback_reason=f"summarize_failed: {e}",
                grounded_source=grounded_source,
            )
