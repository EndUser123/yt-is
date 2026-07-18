#!/usr/bin/env python3
"""NotebookLM transcript importer — extract from existing notebooks.

This module extracts YouTube transcripts from existing NotebookLM notebooks
and imports them into the local transcripts.sqlite database.

Usage:
    python -m csf.csf_nlm_import --dry-run
    python -m csf.csf_nlm_import --run
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add packages root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from csf import nlm_auth_guard
from csf.cache import set_cached_transcript, has_cached_transcript

# NotebookLM notebooks containing YouTube transcripts
YOUTUBE_NOTEBOOKS = {
    "yt-Universe of AI": "852ffa34-32b3-45ea-b9c2-47e2cc53e6a7",
    "yt-Lev Selector": "a384432c-2aff-4516-95f5-af171af10947",
    "yt-Luuk Alleman": "5ce6601d-2262-4690-a033-7520e0641960",
    "yt-AI LABS": "6f701ff1-6d50-45a7-b6ab-f4f6c0daed1d",
    "yt-Sean Kochel": "b9460dae-a7cc-49a0-9a1b-364c53ef38e1",
    "yt-AI Stack Studio": "54f48773-c623-4751-be2d-1b6289ff30ac",
    "yt-Chase AI": "5d95cffd-365b-4906-b3cc-f82fd4a98e06",
}


def run_nlm_query(notebook_id: str, prompt: str) -> dict:
    """Run nlm notebook query and return JSON result."""
    result = nlm_auth_guard.run_nlm(
        nlm_auth_guard.add_profile_args(["notebook", "query", notebook_id, prompt, "--json"]),
        timeout_s=300,
    )

    if result.returncode != 0:
        return {"error": result.stderr}

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "Failed to parse JSON response"}


def _extract_account(stdout: str, stderr: str = "") -> str:
    for line in f"{stdout}\n{stderr}".splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("account:"):
            return stripped.split(":", 1)[1].strip().lower()
    return ""


def check_auth() -> bool:
    """Verify nlm authentication is valid."""
    profile = os.getenv("NOTEBOOKLM_PROFILE", "").strip()
    expected_email = os.getenv("YTIS_NLM_EXPECTED_EMAIL", "").strip().lower()
    result = nlm_auth_guard.run_nlm(
        nlm_auth_guard.add_profile_args(["login", "--check"], profile),
        timeout_s=300,
    )
    if result.returncode != 0:
        return False
    if expected_email and _extract_account(result.stdout or "", result.stderr or "") != expected_email:
        return False
    return True


def ensure_auth() -> None:
    """Re-authenticate if session expired."""
    if not check_auth():
        print("[AUTH] Session expired, re-authenticating...")
        profile = os.getenv("NOTEBOOKLM_PROFILE", "").strip()
        expected_email = os.getenv("YTIS_NLM_EXPECTED_EMAIL", "").strip().lower()
        result = nlm_auth_guard.run_nlm(
            nlm_auth_guard.add_profile_args(["login", "--force"], profile),
            timeout_s=120,
        )
        if result.returncode != 0:
            print(f"[AUTH] Failed to re-authenticate: {result.stderr}")
            raise RuntimeError("Authentication failed")
        if expected_email and _extract_account(result.stdout or "", result.stderr or "") != expected_email:
            raise RuntimeError(
                f"Authentication returned {_extract_account(result.stdout or '', result.stderr or '') or '<missing account>'}, "
                f"expected {expected_email}"
            )
        print("[AUTH] Re-authenticated successfully")


def get_video_list(notebook_id: str) -> list[dict]:
    """Get list of videos from a notebook.

    Returns list of dicts with source_id, title.
    """
    # Get the actual source list from nlm
    sources_result = nlm_auth_guard.run_nlm(
        nlm_auth_guard.add_profile_args(["source", "list", notebook_id, "--json"]),
        timeout_s=300,
    )

    if sources_result.returncode != 0:
        print(f"  Error getting sources: {sources_result.stderr}")
        return []

    # Parse JSON from stdout
    try:
        # nlm CLI may output extra content, extract JSON array
        stdout = sources_result.stdout.strip()
        # Find JSON array start/end
        start = stdout.find('[')
        end = stdout.rfind(']') + 1
        if start >= 0 and end > start:
            json_str = stdout[start:end]
            sources = json.loads(json_str)
        else:
            sources = json.loads(stdout)
    except json.JSONDecodeError as e:
        print(f"  Error parsing source list: {e}")
        print(f"  stdout was: {stdout[:200]}...")
        return []

    return [{"source_id": s["id"], "title": s["title"]} for s in sources]


def extract_transcript(notebook_id: str, video_title: str) -> str | None:
    """Extract full transcript for a video.

    Args:
        notebook_id: NotebookLM notebook ID
        video_title: Exact video title

    Returns:
        Transcript text or None if failed
    """
    # Ensure authentication is valid before querying
    ensure_auth()

    prompt = f"Extract the COMPLETE FULL transcript for '{video_title}'. Return every single word spoken in the video from beginning to end. Do not summarize - return the raw transcript text."
    result = run_nlm_query(notebook_id, prompt)

    if "error" in result:
        # Check if it's an auth error and retry once
        if "Authentication" in str(result.get('error', '')):
            print("  [AUTH] Retrying after re-authentication...")
            ensure_auth()
            result = run_nlm_query(notebook_id, prompt)
            if "error" in result:
                print(f"  Error extracting transcript: {result['error']}")
                return None
        else:
            print(f"  Error extracting transcript: {result['error']}")
            return None

    answer = result.get("value", {}).get("answer", "")
    if not answer or "provided sources do not contain" in answer.lower():
        return None

    return answer


def _extract_real_video_id_from_title(title: str) -> str | None:
    """Find a real YouTube video_id (11 chars, [A-Za-z0-9_-]) embedded in title.

    NotebookLM source titles frequently include the YouTube video id, e.g.
    "Some Title (dQw4w9WgXcQ)" or "Some Title - dQw4w9WgXcQ". This is the
    only safe bind the importer has; the NotebookLM ``source_id`` is opaque
    and hashing it (MD5) produces synthetic keys that pollute the shared
    transcript cache.
    """
    if not title:
        return None
    matches = re.findall(r"[A-Za-z0-9_-]{11}", title or "")
    if not matches:
        return None
    candidate = matches[-1]
    # Skip obviously all-uppercase hex (likely MD5 of source_id, not real id).
    if re.fullmatch(r"[0-9A-F]{11}", candidate):
        return None
    return candidate


def source_id_to_video_id(source_id: str) -> str:
    """Convert NotebookLM source ID to 11-char video ID.

    Uses MD5 hash truncated to 11 chars (alphanumeric only).
    This ensures compatibility with cache.py validation.

    C2 trust-floor: this synthetic key MUST NOT be written to the shared
    transcript cache. Callers that still need to dedupe by source_id should
    use a local store; downstream cache writes must use a real YouTube
    video_id obtained via ``_extract_real_video_id_from_title``.
    """
    # Hash the source ID
    hash_obj = hashlib.md5(source_id.encode())
    hex_digest = hash_obj.hexdigest()

    # Convert to base62-like format (alphanumeric) and truncate to 11
    # Use only first 11 chars of hex digest, replacing non-alphanumeric
    video_id = ""
    for char in hex_digest[:11]:
        if char.isalnum():
            video_id += char.upper()
        else:
            # Replace with 'X' if somehow non-alphanumeric gets through
            video_id += "X"

    # Pad to 11 chars if needed
    while len(video_id) < 11:
        video_id += "0"

    return video_id[:11]


def import_notebook_transcripts(
    notebook_name: str, notebook_id: str, dry_run: bool = False
) -> dict:
    """Import all transcripts from a notebook.

    Returns dict with stats: total, imported, skipped, failed
    """
    print(f"\nProcessing {notebook_name}...")

    # Ensure authentication before starting
    ensure_auth()

    # Get video list
    videos = get_video_list(notebook_id)
    if not videos:
        print(f"  No videos found")
        return {"total": 0, "imported": 0, "skipped": 0, "refused": 0, "failed": 0}

    print(f"  Found {len(videos)} videos")

    stats = {"total": len(videos), "imported": 0, "skipped": 0, "refused": 0, "failed": 0}

    for i, video in enumerate(videos, 1):
        source_id = video["source_id"]
        title = video["title"]
        real_video_id = _extract_real_video_id_from_title(title)
        if not real_video_id:
            # C2 trust-floor: refuse to write a synthetic key. Record refusal
            # distinctly so callers can fix titles / re-run with a separate
            # bind pass instead of seeing the row silently dropped.
            print(
                f"  [{i}/{len(videos)}] REFUSED (no real video_id in title): "
                f"{title[:50]}...",
                flush=True,
            )
            stats["refused"] += 1
            continue
        video_id = real_video_id

        # Check if already cached
        if has_cached_transcript(video_id):
            print(f"  [{i}/{len(videos)}] SKIP: {title[:50]}...", flush=True)
            stats["skipped"] += 1
            continue

        # Rate limiting: delay before each query to avoid NotebookLM API limits
        time.sleep(2)

        # Extract transcript
        print(f"  [{i}/{len(videos)}] Extracting: {title[:50]}...", end=" ", flush=True)
        if dry_run:
            print("DRY RUN", flush=True)
            stats["imported"] += 1
            continue

        transcript = extract_transcript(notebook_id, title)

        if not transcript:
            print("FAILED", flush=True)
            stats["failed"] += 1
            continue

        # Cache the transcript with a verified real video_id.
        try:
            set_cached_transcript(
                video_id,
                "en",
                "notebooklm",
                transcript,
                metadata={
                    "notebook_name": notebook_name,
                    "notebook_id": notebook_id,
                    "source_id": source_id,
                    "source_title": title,
                    "source_video_id": video_id,
                    "importer": "csf_nlm_import",
                },
                bind_verified=True,
            )
            print("OK", flush=True)
            stats["imported"] += 1
        except Exception as e:
            print(f"ERROR: {e}", flush=True)
            stats["failed"] += 1

        # Progress checkpoint every 10 videos (for long-running notebooks)
        if i % 10 == 0:
            print(f"  [Checkpoint] Progress: {i}/{len(videos)} | Imported: {stats['imported']} | Skipped: {stats['skipped']} | Failed: {stats['failed']}", flush=True)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import YouTube transcripts from NotebookLM notebooks"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without importing"
    )
    parser.add_argument(
        "--notebook",
        type=str,
        help="Specific notebook name (default: all yt-* notebooks)",
    )

    args = parser.parse_args()

    # Filter notebooks if specified
    notebooks = YOUTUBE_NOTEBOOKS
    if args.notebook:
        if args.notebook in YOUTUBE_NOTEBOOKS:
            notebooks = {args.notebook: YOUTUBE_NOTEBOOKS[args.notebook]}
        else:
            print(f"Notebook '{args.notebook}' not found")
            print(f"Available: {', '.join(YOUTUBE_NOTEBOOKS.keys())}")
            return

    print("=" * 60)
    print("NotebookLM Transcript Importer")
    print("=" * 60)
    print(f"Mode: {'DRY RUN' if args.dry_run else 'IMPORT'}")
    print(f"Notebooks: {len(notebooks)}")

    # Import from each notebook
    total_stats = {"total": 0, "imported": 0, "skipped": 0, "failed": 0}

    for name, nb_id in notebooks.items():
        stats = import_notebook_transcripts(name, nb_id, args.dry_run)
        for key in total_stats:
            total_stats[key] += stats[key]

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total videos:     {total_stats['total']}")
    print(f"Imported:        {total_stats['imported']}")
    print(f"Skipped (cached): {total_stats['skipped']}")
    print(f"Failed:           {total_stats['failed']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
