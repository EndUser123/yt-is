"""Read-layer failure taxonomy over authoritative ``failure_reason`` strings.

The DB stores semi-typed prose composites (decision packet §C Q17/Q18):
the same semantic class appears under multiple spellings with embedded
UUIDs, which breaks naive GROUP BY analysis. This module maps every
observed spelling to a bounded class *without touching any producer*.

Auth rule (AGENTS.md): a string that merely looks auth-ish is classified
``auth_string_unverified`` and can never by itself produce AUTH_BLOCKED;
auth verdicts come only from typed probes (auth_preflight blocks,
keepalive receipts, nlm_auth events).
"""

from __future__ import annotations

import re

# Bounded read-layer classes. Aligned with the producer taxonomy in
# csf/transcript.py::_classify_failure minus the auth branch, plus the
# NLM-batch composites the producer writes but never classifies.
CLASSES = (
    "source_add_failed.rpc9",
    "source_add_failed.rpc5",
    "source_add_failed.rpc13",
    "source_add_failed.other_rpc",
    "source_add_failed",
    "materialization_terminal_error",
    "materialization_timeout",
    "content_below_threshold",
    "content_fetch_failed",
    "list_failed",
    "quota_exceeded",
    "region_block",
    "timeout",
    "no_transcript",
    "empty_transcript",
    "unavailable",
    "cookie_source",
    "auth_string_unverified",
    "fallback_quality",
    "unknown",
)

_SOURCE_ADD_PREFIX = re.compile(r"^source add failed", re.IGNORECASE)
_MATERIALZATION_TERMINAL = re.compile(r"materialization terminal error", re.IGNORECASE)
_RPC_CODE = re.compile(r"rpc_code=(\d+)", re.IGNORECASE)
_FETCH_FAILED = re.compile(
    r"^fetch failed for (?P<uuid>[0-9a-fA-F-]{36}): (?P<class>\S+)", re.IGNORECASE
)


def classify_failure(reason: str | None) -> str:
    """Map one authoritative failure_reason string to a bounded class."""
    if not reason or not reason.strip():
        return "unknown"
    text = reason.strip()

    if _SOURCE_ADD_PREFIX.search(text):
        # Order matters: the materialization-terminal composite carries more
        # information than the bare prefix, and rpc codes refine both.
        if _MATERIALZATION_TERMINAL.search(text):
            code = _rpc_code(text)
            if code == "9":
                return "source_add_failed.rpc9"
            if code == "5":
                return "source_add_failed.rpc5"
            if code == "13":
                return "source_add_failed.rpc13"
            if code is not None:
                return "source_add_failed.other_rpc"
            return "materialization_terminal_error"
        code = _rpc_code(text)
        if code == "9":
            return "source_add_failed.rpc9"
        if code == "5":
            return "source_add_failed.rpc5"
        if code == "13":
            return "source_add_failed.rpc13"
        if code is not None:
            return "source_add_failed.other_rpc"
        return "source_add_failed"

    match = _FETCH_FAILED.match(text)
    if match:
        inner = match.group("class").lower()
        if inner == "nlm_content_below_threshold":
            return "content_below_threshold"
        if "quota" in inner or "429" in inner:
            return "quota_exceeded"
        if "timeout" in inner or "timed_out" in inner:
            return "timeout"
        return "content_fetch_failed"

    lowered = text.lower()
    if "materialization terminal" in lowered:
        return "materialization_terminal_error"
    if "materialization timeout" in lowered or lowered == "source materialization timeout":
        return "materialization_timeout"
    if lowered == "list failed" or lowered.startswith("list failed"):
        return "list_failed"
    if "quota" in lowered or "429" in lowered or "rate limit" in lowered:
        return "quota_exceeded"
    if "region" in lowered or "geo" in lowered or "not available" in lowered:
        return "region_block"
    if lowered.startswith("terminal:") or " deadline exhausted" in lowered:
        tail = lowered.split(":", 1)[1].strip() if ":" in lowered else lowered.strip()
        if "no_transcript" in tail or "no transcript" in tail:
            return "no_transcript"
        if "unavailable" in tail:
            return "unavailable"
        if "empty" in tail:
            return "empty_transcript"
        return "unknown"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "no transcript" in lowered or "no_transcript" in lowered:
        return "no_transcript"
    if "empty transcript" in lowered or "empty_transcript" in lowered:
        return "empty_transcript"
    if "cookie" in lowered:
        return "cookie_source"
    if "fallback" in lowered and "quality" in lowered:
        return "fallback_quality"
    if "unavailable" in lowered or "deleted" in lowered or "private" in lowered or "not found" in lowered or "404" in lowered:
        return "unavailable"
    if "auth" in lowered or "login" in lowered or "credential" in lowered:
        # Never an auth verdict from a string: typed probes decide.
        return "auth_string_unverified"
    return "unknown"


def _rpc_code(text: str) -> str | None:
    match = _RPC_CODE.search(text)
    return match.group(1) if match else None


def failure_class_group(class_name: str) -> str:
    """Coarse grouping for recurrence views (stage-level attribution)."""
    if class_name.startswith("source_add_failed"):
        return "source_add"
    if class_name in {"content_below_threshold", "content_fetch_failed"}:
        return "content_fetch"
    if class_name in {"materialization_terminal_error", "materialization_timeout"}:
        return "materialization"
    if class_name == "list_failed":
        return "notebook_list"
    return class_name


def classify_rows(rows: list[dict]) -> dict[str, dict]:
    """Aggregate classification over analysis rows with a failure_reason.

    Returns per-class: count, video_ids sample, has_captions split, and
    recurrence samples keyed by the exact original string so a reviewer can
    audit the mapping against raw evidence.
    """
    aggregate: dict[str, dict] = {}
    for row in rows:
        reason = row.get("failure_reason")
        class_name = classify_failure(reason)
        entry = aggregate.setdefault(
            class_name,
            {
                "count": 0,
                "has_captions_0": 0,
                "has_captions_1": 0,
                "has_captions_unknown": 0,
                "example_video_ids": [],
                "example_reasons": [],
            },
        )
        entry["count"] += 1
        captions = row.get("has_captions")
        if captions == 0:
            entry["has_captions_0"] += 1
        elif captions == 1:
            entry["has_captions_1"] += 1
        else:
            entry["has_captions_unknown"] += 1
        if len(entry["example_video_ids"]) < 5:
            entry["example_video_ids"].append(row.get("video_id"))
        if reason and reason not in entry["example_reasons"] and len(entry["example_reasons"]) < 5:
            entry["example_reasons"].append(reason)
    return aggregate
