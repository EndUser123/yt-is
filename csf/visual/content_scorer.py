"""Stage-0 content scorer: which completed videos merit visual analysis?

The visual pipeline exists for videos whose value cannot be gleaned from the
transcript (code on screen, diagrams, charts, slides). This scorer ranks
candidates using ONLY free signals — no download committed:

1. Deixis: screen-referencing narration ("as you can see", "on this slide")
   measured as hits per 1000 transcript words.
2. Title/description keywords: tutorial / demo / walkthrough / code / chart /
   architecture vocabulary.
3. Thumbnail vision probe: CLIP labels over the stored thumbnail (the only
   signal that looks at the video itself).

Deliberately excluded per operator direction (2026-08-18):
- words-per-second (music videos share the narration-sparse signature);
- the below-threshold class as an indicator.

OCR pass-1 in the worker remains the ground-truth confirm after enqueue.
"""

from __future__ import annotations

import re

# Screen-referencing narration. Phrases speakers use when the visual channel
# carries information the words point at rather than contain.
_DEIXIS_PATTERNS = [
    r"as you can see",
    r"you can see (?:here|that|this)",
    r"on (?:this|the) (?:slide|screen|diagram|chart|graph|whiteboard|board)",
    r"in (?:this|the) (?:diagram|chart|graph|figure|screenshot|table)",
    r"if you (?:look|see)",
    r"look at (?:this|that|the)",
    r"let me show",
    r"(?:highlighted|circled|marked|outlined|underlined)",
    r"on the (?:left|right|top|bottom)",
    r"(?:right|over) here",
    r"this (?:section|part|line|piece|block) (?:here|of)",
    r"(?:shown|displayed) (?:here|on)",
    r"the (?:code|snippet|function|output|result) (?:here|on|shows)",
    r"scroll (?:down|up|to)",
    r"zoom (?:in|out)",
    r"as (?:you can )?(?:see|noted|mentioned)",
]
_DEIXIS_RE = re.compile("|".join(f"(?:{p})" for p in _DEIXIS_PATTERNS), re.IGNORECASE)

_TITLE_KEYWORDS = [
    "tutorial", "walkthrough", "demo", "how to", "code", "coding",
    "programming", "vs ", "review", "architecture", "diagram", "dashboard",
    "chart", "analysis", "debug", "fix", "build", "setup", "guide", "screenshot",
    "ui ", "interface", "explains", "visual",
]

# CLIP labels that indicate transcript-irreplacable visual content.
_VISUAL_LABELS = {
    "code screenshot", "diagram", "chart", "slide",
    "architecture drawing", "UI flow", "text overlay", "demo",
}


def score_text(
    transcript: str | None,
    title: str | None = None,
    description: str | None = None,
) -> dict:
    """Score transcript deixis + title/description keywords. Pure function."""
    text = transcript or ""
    words = len(text.split())
    deixis_hits = len(_DEIXIS_RE.findall(text)) if text else 0
    deixis_per_1000 = round(deixis_hits * 1000 / words, 2) if words >= 50 else (
        float(deixis_hits) if deixis_hits else 0.0
    )
    blob = f"{title or ''} {description or ''}".lower()
    keyword_hits = sorted(k.strip() for k in _TITLE_KEYWORDS if k in blob)
    return {
        "transcript_words": words,
        "deixis_hits": deixis_hits,
        "deixis_per_1000": deixis_per_1000,
        "title_keyword_hits": keyword_hits,
        "text_score": min(deixis_per_1000, 20.0) / 20.0 * 0.7
        + min(len(keyword_hits), 3) / 3 * 0.3,
    }


def score_thumbnail(thumbnail_path) -> dict:
    """CLIP-score the stored thumbnail. Graceful without CLIP/model."""
    if not thumbnail_path:
        return {"available": False, "labels": [], "visual_hit": False}
    try:
        from pathlib import Path as _P

        from csf.clip_client import tag_frames

        labels = tag_frames([_P(thumbnail_path)])
    except Exception as exc:
        return {"available": False, "labels": [], "visual_hit": False, "error": f"{type(exc).__name__}"}
    visual_labels = sorted(set(labels) & _VISUAL_LABELS)
    return {
        "available": True,
        "labels": sorted(labels),
        "visual_labels": visual_labels,
        "visual_hit": bool(visual_labels),
    }


def depth_weight(duration_s: float | None = None, transcript_words: int = 0) -> float:
    """Duration/depth prior (operator-approved 2026-08-18).

    The high-value cohort's evidence: strong artifacts came from minutes-long
    tutorials (45-63 frames); the weak tail was sub-60s shorts with thin
    content. Real duration is used when the catalog has it (rare today);
    otherwise transcript length approximates duration at ~130 wpm — a short
    cannot carry a long transcript, so the proxy discriminates the same tail.
    """
    if duration_s and duration_s > 0:
        minutes = duration_s / 60.0
    elif transcript_words > 0:
        minutes = transcript_words / 130.0
    else:
        return 1.0
    if minutes < 1:
        return 0.5
    if minutes < 3:
        return 0.8
    if minutes >= 8:
        return 1.15
    return 1.0


def combined_score(
    text_result: dict,
    thumb_result: dict | None,
    *,
    duration_s: float | None = None,
) -> dict:
    """Transparent additive rubric: thumbnail visual hit is the strongest
    single signal (it looks at the video), deixis rate next, keywords last —
    all scaled by the duration/depth weight."""
    thumb = thumb_result or {}
    weight = depth_weight(duration_s, text_result.get("transcript_words", 0))
    score = text_result.get("text_score", 0.0) * weight
    if thumb.get("visual_hit"):
        score += 0.5
    return {
        "score": round(min(score, 1.5), 4),
        "depth_weight": weight,
        "text": text_result,
        "thumbnail": thumb,
    }
