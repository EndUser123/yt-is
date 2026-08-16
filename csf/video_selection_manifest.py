"""Validated exact-video selection manifests for transcript fetch planning."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone


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
    selection_criteria: dict[str, object] | None = None
    input_database_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class FetchSelection:
    selected_entries: tuple[dict[str, object | None], ...]
    missing_ids: tuple[str, ...]
    non_pending_by_status: dict[str, tuple[str, ...]]
    limit_omitted_ids: tuple[str, ...]
    database_fingerprint: str
    fingerprint: str


_DATABASE_FINGERPRINT_FIELDS = (
    "video_id",
    "status",
    "source",
    "updated_at",
    "has_captions",
)


def _canonical_database_row(row: Mapping[str, object | None] | None) -> dict[str, object | None]:
    """Project callers' DB rows onto one stable selection-fingerprint schema."""
    return {
        field: row.get(field) if row is not None else None
        for field in _DATABASE_FINGERPRINT_FIELDS
    }


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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

    raw_criteria = payload.get("selection_criteria")
    if raw_criteria is not None and not isinstance(raw_criteria, dict):
        raise ValueError("selection_criteria must be an object or null")
    input_database_fingerprint = payload.get("input_database_fingerprint")
    if input_database_fingerprint is not None and not isinstance(input_database_fingerprint, str):
        raise ValueError("input_database_fingerprint must be a string or null")

    return VideoSelectionManifest(
        manifest_version=1,
        generated_at=generated_at.strip(),
        selection_name=selection_name.strip(),
        items=tuple(items),
        fingerprint="sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
        selection_criteria=dict(raw_criteria) if raw_criteria is not None else None,
        input_database_fingerprint=input_database_fingerprint,
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
    database_snapshot = [
        (item.video_id, _canonical_database_row(rows_by_video_id.get(item.video_id)))
        for item in manifest.items
    ]
    database_fingerprint = _sha256_json(database_snapshot)
    selection_payload = {
        "manifest_fingerprint": manifest.fingerprint,
        "database_fingerprint": database_fingerprint,
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
        database_fingerprint=database_fingerprint,
        fingerprint="sha256:" + hashlib.sha256(canonical).hexdigest(),
    )


def build_selection_receipt(
    manifest: VideoSelectionManifest,
    selection: FetchSelection,
    *,
    manifest_path: Path,
    database_path: Path,
    max_items: int | None,
    dry_run: bool,
) -> dict[str, object]:
    """Build a durable, replay-auditable receipt for one manifest selection."""
    return {
        "receipt_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selection_mode": "video_manifest",
        "selection_name": manifest.selection_name,
        "manifest_path": str(manifest_path),
        "manifest_fingerprint": manifest.fingerprint,
        "database_path": str(database_path),
        "database_fingerprint": selection.database_fingerprint,
        # Keep the legacy name for compatibility, but make the logical
        # snapshot scope explicit so receipts are not mistaken for file hashes.
        "database_snapshot_fingerprint": selection.database_fingerprint,
        "database_snapshot_schema": "analysis_status",
        "database_snapshot_scope": "manifest_video_ids_in_manifest_order",
        "selection_fingerprint": selection.fingerprint,
        "fingerprint_semantics": {
            "manifest_fingerprint": "sha256_raw_manifest_json_bytes",
            "input_database_fingerprint": "sha256_canonical_manifest_producer_row_list",
            "database_fingerprint": "sha256_canonical_analysis_status_rows_in_manifest_order",
            "selection_fingerprint": "sha256_canonical_selection_result",
        },
        "selection_criteria": manifest.selection_criteria,
        "input_database_fingerprint": manifest.input_database_fingerprint,
        "max_items": max_items,
        "dry_run": dry_run,
        "manifest_item_count": len(manifest.items),
        "selected_ids": [str(row["video_id"]) for row in selection.selected_entries],
        "selected_count": len(selection.selected_entries),
        "missing_ids": list(selection.missing_ids),
        "missing_count": len(selection.missing_ids),
        "non_pending_by_status": {
            key: list(value) for key, value in selection.non_pending_by_status.items()
        },
        "non_pending_count": sum(len(value) for value in selection.non_pending_by_status.values()),
        "limit_omitted_ids": list(selection.limit_omitted_ids),
        "limit_omitted_count": len(selection.limit_omitted_ids),
    }


def write_selection_receipt(
    path: Path,
    receipt: Mapping[str, object],
    *,
    overwrite: bool = False,
) -> None:
    """Atomically write a selection receipt without replacing it accidentally."""
    _write_json_atomically(
        path,
        receipt,
        overwrite=overwrite,
        error_label="selection receipt",
    )


def _write_json_atomically(
    path: Path,
    payload: Mapping[str, object],
    *,
    overwrite: bool,
    validate: Callable[[Path], object] | None = None,
    error_label: str = "path",
) -> None:
    """Write JSON atomically, using exclusive creation unless replacing is explicit."""
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{error_label} exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(dict(payload), temp_file, indent=2, sort_keys=True)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)
        if validate is not None:
            validate(temp_path)
        if overwrite:
            os.replace(temp_path, path)
            temp_path = None
        else:
            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise FileExistsError(f"{error_label} exists: {path}") from exc
            temp_path.unlink()
            temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def read_selection_receipt(path: Path) -> dict[str, object]:
    """Read and minimally validate a previously written selection receipt."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("receipt_version") != 1:
        raise ValueError("invalid selection receipt")
    required = ("manifest_fingerprint", "database_fingerprint", "selection_fingerprint")
    missing = [key for key in required if not isinstance(payload.get(key), str)]
    if missing:
        raise ValueError(f"selection receipt missing fields: {', '.join(missing)}")
    return payload


def verify_selection_receipt(
    receipt: Mapping[str, object],
    manifest: VideoSelectionManifest,
    selection: FetchSelection,
) -> None:
    """Fail closed when a receipt no longer describes the current selection."""
    expected = {
        "manifest_fingerprint": manifest.fingerprint,
        "database_fingerprint": selection.database_fingerprint,
        "selection_fingerprint": selection.fingerprint,
    }
    mismatches = [
        key for key, value in expected.items()
        if receipt.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "selection receipt does not match current manifest/database: "
            + ", ".join(mismatches)
        )


def write_video_selection_manifest(
    path: Path,
    payload: Mapping[str, object],
    *,
    overwrite: bool = False,
) -> None:
    """Atomically write a validated manifest payload."""
    _write_json_atomically(
        path,
        payload,
        overwrite=overwrite,
        validate=load_video_selection_manifest,
        error_label="manifest",
    )
