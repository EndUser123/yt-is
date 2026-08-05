"""Validated exact-video selection manifests for transcript fetch planning."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True, slots=True)
class VideoSelectionItem:
    video_id: str
    source_note: str | None = None


@dataclass(frozen=True, slots=True)
class VideoSelectionManifest:
    manifest_version: int
    generated_at: str
    selection_name: str
    items: tuple[VideoSelectionItem, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class FetchSelection:
    selected_entries: tuple[dict[str, object | None], ...]
    missing_ids: tuple[str, ...]
    non_pending_by_status: dict[str, tuple[str, ...]]
    limit_omitted_ids: tuple[str, ...]
    fingerprint: str


def load_video_selection_manifest(path: Path) -> VideoSelectionManifest:
    """Load and validate a versioned exact-video selection manifest."""
    raw_bytes = path.read_bytes()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid video selection manifest JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("video selection manifest root must be an object")
    if payload.get("manifest_version") != 1:
        raise ValueError("video selection manifest_version must be 1")
    generated_at = payload.get("generated_at")
    selection_name = payload.get("selection_name")
    if not isinstance(generated_at, str) or not generated_at.strip():
        raise ValueError("generated_at must be a non-empty string")
    if not isinstance(selection_name, str) or not selection_name.strip():
        raise ValueError("selection_name must be a non-empty string")
    raw_videos = payload.get("videos")
    if not isinstance(raw_videos, list):
        raise ValueError("videos must be a list")

    items: list[VideoSelectionItem] = []
    seen: set[str] = set()
    for index, raw_item in enumerate(raw_videos):
        if not isinstance(raw_item, dict):
            raise ValueError(f"videos[{index}] must be an object")
        video_id = raw_item.get("video_id")
        if not isinstance(video_id, str) or not _VIDEO_ID_RE.fullmatch(video_id):
            raise ValueError(f"videos[{index}].video_id must be an 11-character YouTube ID")
        if video_id in seen:
            raise ValueError(f"duplicate video_id: {video_id}")
        source_note = raw_item.get("source_note")
        if source_note is not None and not isinstance(source_note, str):
            raise ValueError(f"videos[{index}].source_note must be a string or null")
        seen.add(video_id)
        items.append(VideoSelectionItem(video_id, source_note.strip() if source_note else None))

    return VideoSelectionManifest(
        manifest_version=1,
        generated_at=generated_at.strip(),
        selection_name=selection_name.strip(),
        items=tuple(items),
        fingerprint="sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
    )


def select_manifest_entries(
    manifest: VideoSelectionManifest,
    rows_by_video_id: Mapping[str, dict[str, object | None]],
    *,
    max_items: int | None = None,
) -> FetchSelection:
    """Select pending DB rows in manifest order without changing their data."""
    if max_items is not None and max_items < 0:
        raise ValueError("max_items must be >= 0")

    selected: list[dict[str, object | None]] = []
    missing: list[str] = []
    non_pending: dict[str, list[str]] = {}
    eligible_ids: list[str] = []

    for item in manifest.items:
        row = rows_by_video_id.get(item.video_id)
        if row is None:
            missing.append(item.video_id)
            continue
        status = str(row.get("status") or "unknown")
        if status != "pending":
            non_pending.setdefault(status, []).append(item.video_id)
            continue
        eligible_ids.append(item.video_id)
        if max_items is None or len(selected) < max_items:
            selected.append(dict(row))

    selected_ids = {str(row["video_id"]) for row in selected}
    limit_omitted = [video_id for video_id in eligible_ids if video_id not in selected_ids]
    selection_payload = {
        "manifest_fingerprint": manifest.fingerprint,
        "selected_ids": [str(row["video_id"]) for row in selected],
        "missing_ids": missing,
        "non_pending_by_status": non_pending,
        "limit_omitted_ids": limit_omitted,
    }
    canonical = json.dumps(selection_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return FetchSelection(
        selected_entries=tuple(selected),
        missing_ids=tuple(missing),
        non_pending_by_status={key: tuple(value) for key, value in non_pending.items()},
        limit_omitted_ids=tuple(limit_omitted),
        fingerprint="sha256:" + hashlib.sha256(canonical).hexdigest(),
    )
