"""Gemini full-video/frames artifact extraction (the vision tier).

The operator's reference workflow: given a video (or its frames), produce the
code files, prompts, and graph/chart descriptions displayed on screen — the
information the transcript cannot carry.

API keys resolve with fallback: ``GEMINI_API_KEY`` first (house default),
then ``GEMINI_API_KEY_1`` / ``_2`` (verified working 2026-08-19; the primary
key is currently invalid, so fallback is load-bearing). Keys come from the
workspace .env via ``csf.paths.load_workspace_env``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

KEY_NAMES = ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2")

EXTRACTION_PROMPT = """\
You are given frames from a video, in chronological order. Produce TWO parts.

PART 1 — CODE AND TEXT ARTIFACTS (transcription, not summary):
- Transcribe code, commands, prompts, and file content EXACTLY as displayed:
  every line, character for character. Do NOT summarize, abbreviate,
  paraphrase, or truncate anything.
- The full text IS readable in the images. If any line seems hard to read,
  look again carefully before transcribing it.
- One markdown section per artifact (code file, terminal, prompt, config),
  artifact type as header.
- Charts/graphs: describe axes, series, and every readable value.
- Diagrams: describe nodes, edges, and labels.

PART 2 — WORKFLOW AND UI DOCUMENTATION:
- Document what the application/agent is DOING across the frames: workflow
  steps, progress indicators, status bars, file-change diffs (+/- counts),
  modals and confirmations, and visible navigation state.
- Capture specifics: diff statistics, file names, status text, button labels.

Rules: work image by image; skip faces, subscribe buttons, branding.
End with: "Artifacts: N. Workflow events: M."

(Prompt engineering notes, 2026-08-19: (1) Never instruct models to insert
cut-off/truncation markers — 3.x models obey so literally that dense code
comes back shredded while 2.5-flash ignores it; explicit anti-truncation
language is what produces clean transcription. (2) This two-part prompt
replaced the dual-engine approach: transcription-only prompts yield
code-deep/UI-shallow output, and adding Part 2 recovers the workflow
dimension in the same single call.)
"""


def resolve_api_key() -> tuple[str, str] | None:
    """First present key (value, env-name)."""
    for name in KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return None


def iter_clients():
    """Yield (client, key_name), skipping keys that fail on first use."""
    from google import genai

    for name in KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        try:
            client = genai.Client(api_key=value)
            yield client, name
        except Exception:
            continue


def _frame_parts(frame_paths: list[Path], max_frames: int = 16):
    from google.genai import types

    parts = []
    for path in frame_paths[:max_frames]:
        parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type="image/jpeg"))
    return parts


def extract_via_agy(prompt: str, *, print_timeout_s: str = "10m") -> dict:
    """Run the extraction through the Antigravity CLI (agy) headless.

    Preferred engine per operator (2026-08-19): agy rides the Google-account
    quota (AI Pro) instead of API billing. Prompt goes via stdin (the
    positional-prompt form trips argument parsing in 1.1.15). Any failure —
    including the account-quota 429 — returns {"ok": False} so the caller
    falls back to API keys.
    """
    import shutil as _shutil
    import subprocess as _sp

    agy = _shutil.which("agy")
    if not agy:
        return {"ok": False, "error": "agy not on PATH"}
    try:
        proc = _sp.run(
            [agy, "--print", "--print-timeout", print_timeout_s],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=660,
            cwd=str(Path.home()),
        )
    except _sp.TimeoutExpired:
        return {"ok": False, "error": "agy print timeout"}
    text = (proc.stdout or "").strip()
    if proc.returncode == 0 and text and "Error:" not in text[:200]:
        return {"ok": True, "engine": "agy", "markdown": dedup_fenced_blocks(text)}
    return {
        "ok": False,
        "error": f"agy rc={proc.returncode}: {(proc.stderr or text)[:200]}",
    }


def _agy_output_is_task(markdown: str) -> bool:
    """Distinguish real extraction output from agy meta-text failures.

    agy occasionally responds about its own CLI flags or tooling instead of
    the task (observed 2026-08-19: output explaining --print-timeout on an
    extraction request). Real extractions reference PART markers or contain
    fenced code blocks; meta-text talks about CLI flags, scripts, or tools.
    """
    if not markdown or len(markdown) < 100:
        return False
    lowered = markdown.lower()
    # Meta-text tells: agy discussing its own invocation.
    meta_markers = ("--print-timeout", "--dangerously-skip", "antigravity cli",
                    "flag", "pytest", "argparse", "click")
    meta_hits = sum(1 for m in meta_markers if m in lowered)
    # Task tells: the combined prompt produces these patterns.
    task_markers = ("part 1", "part 2", "```", "code file", "terminal",
                    "workflow", "artifact")
    task_hits = sum(1 for m in task_markers if m in lowered)
    return task_hits >= 2 and meta_hits == 0


def dedup_fenced_blocks(markdown: str) -> str:
    """Collapse consecutive identical fenced code blocks (review F-11).

    Tutorial-style videos often show the same file across consecutive frames;
    the extraction reproduces it verbatim each time. Hash each fenced block
    and drop immediate repeats — shrinks artifacts ~30-50% and improves
    downstream embedding quality.
    """
    import hashlib
    import re

    lines = markdown.split("\n")
    out: list[str] = []
    in_fence = False
    current_fence: list[str] = []
    last_fence_hash: str | None = None

    for line in lines:
        if line.strip().startswith("```"):
            if not in_fence:
                in_fence = True
                current_fence = [line]
            else:
                in_fence = False
                current_fence.append(line)
                fence_hash = hashlib.md5(
                    "\n".join(current_fence).encode("utf-8")
                ).hexdigest()
                if fence_hash != last_fence_hash:
                    out.extend(current_fence)
                    last_fence_hash = fence_hash
                # else: skip — identical to the previous fenced block
        elif in_fence:
            current_fence.append(line)
        else:
            out.append(line)
            if line.strip() and not line.strip().startswith("#"):
                last_fence_hash = None  # reset on non-header prose between blocks

    return "\n".join(out)


def extract_artifacts_from_frames(
    frame_paths: list[Path],
    *,
    model: str = "gemini-2.5-flash",
    max_frames: int = 16,
) -> dict:
    """Send frames to Gemini and return the extracted artifact markdown.

    Tries each configured key until one succeeds; a 4xx from an invalid key
    moves to the next. Returns {"ok": True, "markdown": ..., "model": ...,
    "key_name": ...} or {"ok": False, "error": ...}.
    """
    if not frame_paths:
        return {"ok": False, "error": "no_frames"}

    agy_error: str | None = None
    # Engine order (operator preference, verified 2026-08-19): agy FIRST —
    # rides the Google AI Pro subscription pools (dashboard: 90% weekly /
    # 95% five-hour remaining) instead of free-tier API keys that hit 503
    # load spikes. Quality is competitive with the fixed anti-truncation
    # prompt (agy 3,645 chars / 0 cut-offs vs API 4,261 / 0). API keys are
    # the fallback. Set YTIS_VISUAL_EXTRACT_ENGINE=api-first to invert.
    if os.environ.get("YTIS_VISUAL_EXTRACT_ENGINE", "agy-first") == "agy-first":
        frame_dir = frame_paths[0].parent
        ordered = sorted(frame_paths, key=lambda p: p.name)
        agy_prompt = (
            f"Read the {len(ordered)} JPEG images inside {frame_dir} "
            "(chronological by filename). " + EXTRACTION_PROMPT
        )
        agy_result = extract_via_agy(agy_prompt)
        if agy_result.get("ok") and _agy_output_is_task(agy_result["markdown"]):
            return {
                "ok": True,
                "markdown": dedup_fenced_blocks(agy_result["markdown"]),
                "engine": "agy",
                "frames_sent": len(ordered),
            }
        agy_error = (
            f"quality gate rejected: output discusses CLI/tooling instead of "
            f"frames ({agy_result.get('error') or 'non-task output'})"
            if agy_result.get("ok")
            else agy_result.get("error")
        )

    last_error = "no usable key"
    for client, key_name in iter_clients():
        try:
            response = client.models.generate_content(
                model=model,
                contents=[EXTRACTION_PROMPT, *_frame_parts(frame_paths, max_frames)],
            )
            text = (response.text or "").strip()
            if text:
                return {
                    "ok": True,
                    "markdown": dedup_fenced_blocks(text),
                    "model": model,
                    "key_name": key_name,
                    "frames_sent": min(len(frame_paths), max_frames),
                }
            last_error = f"empty response via {key_name}"
        except Exception as exc:
            last_error = f"{key_name}: {type(exc).__name__}: {str(exc)[:200]}"
            continue
    error = last_error
    if os.environ.get("YTIS_VISUAL_EXTRACT_ENGINE", "agy-first") == "agy-first":
        error = f"agy unavailable ({agy_error}); api: {last_error}"
    return {"ok": False, "error": error}
