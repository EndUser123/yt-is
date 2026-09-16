"""Prompt-boundary handling for untrusted source text.

Source text remains unchanged in grounded artifacts.  This module only
redacts common instruction-boundary markers before source text is embedded in
a provider prompt, and deliberately never truncates the source projection.
"""

from __future__ import annotations

import re


_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"disregard\s+(all\s+)?prior\s+(instructions?|commands?)", re.I),
    re.compile(r"forget\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an)\s+", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"<\|(?:system|user|assistant)\|>", re.I),
)


def sanitize_source_for_prompt(text: str) -> str:
    """Redact common prompt-injection markers without dropping source ranges."""
    if not isinstance(text, str):
        raise ValueError("source prompt text must be a string")
    sanitized = text
    for pattern in _PROMPT_INJECTION_PATTERNS:
        sanitized = pattern.sub("[redacted]", sanitized)
    return sanitized
