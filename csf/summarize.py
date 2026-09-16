"""LLM direct summarization: transcript + code snippets + visual tags -> VideoAnalysisResult.

Zero external API cost — uses the Gemini CLI already in the environment.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import re

from csf.providers import VideoAnalysisResult
from csf.prompt_safety import sanitize_source_for_prompt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The complete transcript is intentionally passed through.  If a provider
# cannot accept the resulting prompt, the caller receives an explicit
# fallback rather than a summary of an undisclosed suffix.


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def summarize(
    transcript: str,
    code_snippets: list[str],
    visual_tags: list[str],
    timeout: float = 120.0,
) -> VideoAnalysisResult:
    """Summarize video content via direct LLM call.

    Args:
        transcript: Full video transcript text.
        code_snippets: OCR-captured on-screen code strings.
        visual_tags: CLIP-captured visual label strings.
        timeout: Subprocess timeout in seconds.

    Returns:
        VideoAnalysisResult with mode="transcript" on any failure,
        mode="summarize" on success.
    """
    # ---- Build prompt with separate sections ----
    def prompt_items(items) -> str:
        """Render source-derived list items through the prompt boundary."""
        if not items:
            return "None"
        rendered = []
        for item in items:
            text = item if isinstance(item, str) else str(item)
            rendered.append(f"- {sanitize_source_for_prompt(text)}")
        return "\n".join(rendered)

    snippets_block = prompt_items(code_snippets)
    tags_block = prompt_items(visual_tags)

    prompt = (
        "Analyze this video and extract structured information.\n\n"
        "## TRANSCRIPT\n"
        f"{sanitize_source_for_prompt(transcript)}\n\n"
        "## CODE SNIPPETS (on-screen code)\n"
        f"{snippets_block}\n\n"
        "## VISUAL TAGS\n"
        f"{tags_block}\n\n"
        "Return ONLY valid JSON with these exact keys:\n"
        '- "title": string\n'
        '- "summary": 2-3 sentence string\n'
        '- "key_topics": list of 5 strings\n'
        '- "key_points": list of 3 strings\n\n'
        "Return ONLY the JSON, no explanation or markdown formatting."
    )

    # ---- Locate Gemini CLI ----
    gemini_path = shutil.which("gemini")
    if not gemini_path:
        return _fail("gemini_cli_not_found")

    # ---- Run Gemini CLI ----
    try:
        result = subprocess.run(
            [gemini_path, "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return _fail(f"summarize_timeout_{timeout:.0f}s")

    # ---- Handle non-zero exit ----
    if result.returncode != 0:
        return _fail(f"summarize_nonzero_exit_{result.returncode}")

    # ---- Parse JSON ----
    text = result.stdout.strip()

    # Try direct parse
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try stripping markdown code blocks
        cleaned = re.sub(r"```(?:json)?\n?|```", "", text).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return _fail("llm_parse_error")

    # ---- Extract fields ----
    return VideoAnalysisResult(
        title=parsed.get("title", "Unknown") or "Unknown",
        summary=parsed.get("summary", "") or "",
        key_topics=parsed.get("key_topics", []) or [],
        key_points=parsed.get("key_points", []) or [],
        code_snippets=code_snippets,
        visual_tags=visual_tags,
        mode="summarize",
        fallback_reason=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fail(reason: str) -> VideoAnalysisResult:
    """Return a fallback VideoAnalysisResult on error."""
    return VideoAnalysisResult(
        title="Unknown",
        summary="",
        key_topics=[],
        key_points=[],
        code_snippets=[],
        visual_tags=[],
        mode="transcript",
        fallback_reason=f"summarize_failed: {reason}",
    )
