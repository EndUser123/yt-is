"""Browser ownership manifest helpers for browser-health gating."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable


BROWSER_OWNERSHIP_MANIFEST_NAME = "browser_ownership.json"


@dataclass(frozen=True, slots=True)
class BrowserOwnershipRecord:
    lane: str
    browser_profile_root: str
    browser_profile_directory: str
    browser_profile_namespace: str

    def to_dict(self) -> dict[str, str]:
        return {
            "lane": self.lane,
            "browser_profile_root": self.browser_profile_root,
            "browser_profile_directory": self.browser_profile_directory,
            "browser_profile_namespace": self.browser_profile_namespace,
        }


@dataclass(frozen=True, slots=True)
class BrowserOwnershipManifest:
    manifest_version: int
    generated_at: str
    run_root: str | None
    run_environment_label: str | None
    default_browser_profile_root: str
    owned_browser_roots: tuple[BrowserOwnershipRecord, ...]

    @property
    def allowed_browser_roots(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    record.browser_profile_root.strip()
                    for record in self.owned_browser_roots
                    if record.browser_profile_root.strip()
                }
            )
        )


def build_browser_ownership_manifest(
    *,
    run_root: Path,
    owned_browser_roots: Iterable[dict[str, Any]],
    default_browser_profile_root: str | Path,
    run_environment_label: str | None = None,
) -> dict[str, Any]:
    records = tuple(_normalize_record(item, index=index) for index, item in enumerate(owned_browser_roots))
    return {
        "manifest_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_root": str(run_root),
        "run_environment_label": str(run_environment_label or ""),
        "default_browser_profile_root": str(default_browser_profile_root),
        "owned_browser_roots": [record.to_dict() for record in records],
    }


def write_browser_ownership_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_browser_ownership_manifest(path: Path) -> BrowserOwnershipManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("browser ownership manifest root must be an object")
    manifest_version = payload.get("manifest_version")
    if manifest_version != 1:
        raise ValueError("browser ownership manifest_version must be 1")
    generated_at = _require_text(payload, "generated_at")
    run_root = _optional_text(payload.get("run_root"))
    run_environment_label = _optional_text(payload.get("run_environment_label"))
    default_browser_profile_root = _require_text(payload, "default_browser_profile_root")
    raw_owned_browser_roots = payload.get("owned_browser_roots")
    if not isinstance(raw_owned_browser_roots, list):
        raise ValueError("owned_browser_roots must be a list")

    owned_browser_roots = tuple(
        _load_record(raw_record, index=index)
        for index, raw_record in enumerate(raw_owned_browser_roots)
    )
    return BrowserOwnershipManifest(
        manifest_version=1,
        generated_at=generated_at,
        run_root=run_root,
        run_environment_label=run_environment_label,
        default_browser_profile_root=default_browser_profile_root,
        owned_browser_roots=owned_browser_roots,
    )


def _normalize_record(raw_record: dict[str, Any], *, index: int) -> BrowserOwnershipRecord:
    if not isinstance(raw_record, dict):
        raise ValueError(f"owned_browser_roots[{index}] must be an object")
    lane = _require_text(raw_record, "lane", index=index)
    browser_profile_root = _require_text(raw_record, "browser_profile_root", index=index)
    browser_profile_directory = _optional_text(raw_record.get("browser_profile_directory")) or ""
    browser_profile_namespace = _require_text(raw_record, "browser_profile_namespace", index=index)
    return BrowserOwnershipRecord(
        lane=lane,
        browser_profile_root=browser_profile_root,
        browser_profile_directory=browser_profile_directory,
        browser_profile_namespace=browser_profile_namespace,
    )


def _load_record(raw_record: dict[str, Any], *, index: int) -> BrowserOwnershipRecord:
    return _normalize_record(raw_record, index=index)


def _require_text(raw: dict[str, Any], key: str, *, index: int | None = None) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        if index is None:
            raise ValueError(f"{key} must be a non-empty string")
        raise ValueError(f"owned_browser_roots[{index}].{key} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    raise ValueError("optional text fields must be strings or null")
