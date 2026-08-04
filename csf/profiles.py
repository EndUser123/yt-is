"""Quality profiles for video analysis (DEC-08).

Profile promotion is OCR-output-driven: the `standard` profile runs first
(cheap), and if OCR detects code-heavy content, the profile is promoted
to `visual` for Gemini full-video analysis on the next iteration.

This module is unit-testable in isolation — no external dependencies.
"""

from __future__ import annotations

import re
from enum import Enum


class Profile(str, Enum):
    """Analysis quality tiers.

    - transcript: text-only, skip visual entirely (podcasts, talking heads)
    - standard: transcript + CRV frames + OCR + CLIP (default for every video)
    - visual: full-video Gemini multimodal (promoted when OCR detects code)
    """

    transcript = "transcript"
    standard = "standard"
    visual = "visual"


# Code-detection patterns applied to OCR output (DEC-08).
# These detect code markers that indicate visual content worth full-video analysis.
_CODE_FENCE_RE = re.compile(r"```", re.MULTILINE)
_LANGUAGE_KEYWORDS = [
    "def ", "class ", "function ", "import ", "#include",
    "func ", "const ", "let ", "var ", "return ",
    "public ", "private ", "void ", "int ", "string ",
]
_TERMINAL_PATTERNS = [
    r"^\$\s",          # shell prompt
    r"^>\s",           # REPL prompt
    r"Traceback",      # Python error
    r"Error:",         # generic error
    r"Exception",      # Java/Python exception
]
_TERMINAL_RES = [re.compile(p, re.MULTILINE) for p in _TERMINAL_PATTERNS]

# Punctuation density threshold for code detection.
# Code has high density of {}[]().:;= relative to total characters.
_PUNCTUATION_DENSITY_THRESHOLD = 0.12
_CODE_PUNCTUATION = set("{}[]().:;=<>+-*/&|!")


def _compute_punctuation_density(text: str) -> float:
    """Compute the ratio of code-punctuation characters to total characters."""
    if not text:
        return 0.0
    code_punct_count = sum(1 for c in text if c in _CODE_PUNCTUATION)
    return code_punct_count / len(text)


def promote_profile(ocr_output: str, transcript_text: str = "") -> Profile:
    """Determine the quality profile based on OCR output.

    The `standard` profile runs first (always). If OCR detects code-heavy
    content (code fences, language keywords, terminal output, or high
    punctuation density), the profile is promoted to `visual` for Gemini
    full-video analysis.

    Args:
        ocr_output: Text extracted from video frames via OCR (EasyOCR).
        transcript_text: Optional transcript text for additional context.
                         Currently unused but reserved for future heuristics.

    Returns:
        Profile.visual if code-heavy content detected, Profile.standard otherwise.
    """
    if not ocr_output:
        return Profile.standard

    # Check for code fences (``` markers)
    code_fences = _CODE_FENCE_RE.findall(ocr_output)
    if len(code_fences) >= 2:  # A pair of ``` indicates a code block
        return Profile.visual

    # Check for language keywords
    keyword_hits = sum(
        1 for kw in _LANGUAGE_KEYWORDS if kw in ocr_output
    )
    if keyword_hits >= 2:
        return Profile.visual

    # Check for terminal/output patterns
    terminal_hits = sum(1 for r in _TERMINAL_RES if r.search(ocr_output))
    if terminal_hits >= 1:
        return Profile.visual

    # Check punctuation density (code has high punctuation ratio)
    density = _compute_punctuation_density(ocr_output)
    if density > _PUNCTUATION_DENSITY_THRESHOLD:
        return Profile.visual

    return Profile.standard
