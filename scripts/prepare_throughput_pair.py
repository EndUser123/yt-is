#!/usr/bin/env python3
"""Prepare and reconcile an offline, uncached throughput comparison.

This module never launches a worker.  It reads the canonical queue and an
explicit reference cache read-only, then copies them into isolated staging
directories and removes only the frozen IDs from those copies.  Its packet is
staging-only; use ``scripts/run_throughput_pair.py`` to add executable packet
metadata and to perform the separate, explicit execution step.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable, Mapping

ACCOUNTS = {
    "a.hominidae": {"account_profile": "a.hominidae", "billing_plan": "Pro", "plan": "Pro", "email": "a.hominidae@gmail.com"},
    "troup.hominidae": {"account_profile": "troup.hominidae", "billing_plan": "Free", "plan": "Free", "email": "troup.hominidae@gmail.com"},
    "brsthomson": {"account_profile": "brsthomson", "billing_plan": "Free", "plan": "Free", "email": "brsthomson@hotmail.com"},
}
ACCOUNT_ORDER = tuple(ACCOUNTS)
ARMS = ("control", "adaptive")
COMPARISON_MODES = ("adaptive", "environment")
PAIRS = ("pair-01", "pair-02")
CAPTION_STATES = ("captioned", "unknown", "no-caption", "any")
FORBIDDEN_TEXT = ("fallback", "auth", "source_add", "source-add", "rpc_code")
EXCLUSION_REASON = "benchmark_only_offline_exclusion"
DEFAULT_NLM_BATCH_SIZE = 50
DEFAULT_INDUSTRIAL_BATCHES_PER_WORKER = 4
ACCOUNT_SETTING_KEYS = frozenset(
    {
        "workers_per_account",
        "batch_size",
        "adaptive_workers",
        "adaptive_min_workers",
        "adaptive_max_workers",
        "adaptive_scale_up_backlog",
        "adaptive_scale_down_backlog",
        "adaptive_cooldown_s",
        "adaptive_health_window",
    }
)
_ENVIRONMENT_KEY = re.compile(r"^YTIS_[A-Z0-9_]+$")


def fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _ro(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"SQLite file not found: {path}")
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _integrity(path: Path) -> str:
    with _ro(path) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    if not result or str(result[0]).lower() != "ok":
        raise ValueError(f"SQLite integrity_check failed for {path}: {result!r}")
    return "ok"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _caption_predicate(caption_state: str) -> tuple[str, tuple[object, ...]]:
    if caption_state == "captioned":
        return "has_captions = ?", (1,)
    if caption_state == "no-caption":
        return "has_captions = ?", (0,)
    if caption_state == "unknown":
        return "has_captions IS NULL", ()
    if caption_state == "any":
        return "1 = 1", ()
    raise ValueError(f"unsupported caption_state: {caption_state}")


def _read_candidates(
    db_path: Path,
    *,
    caption_state: str = "captioned",
    exclude_video_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    excluded = {str(video_id) for video_id in exclude_video_ids}
    predicate, parameters = _caption_predicate(caption_state)
    with _ro(db_path) as conn:
        required = {"video_id", "status", "updated_at", "has_captions"}
        missing = required - _table_columns(conn, "analysis_status")
        if missing:
            raise ValueError(f"analysis_status lacks required columns: {sorted(missing)}")
        rows = conn.execute(
            "SELECT video_id, status, updated_at, has_captions, source "
            f"FROM analysis_status WHERE status = 'pending' AND ({predicate}) "
            "ORDER BY updated_at ASC, video_id ASC",
            parameters,
        ).fetchall()
    return [
        {"video_id": str(row[0]), "status": str(row[1]), "updated_at": str(row[2]),
         "has_captions": int(row[3]) if row[3] is not None else None, "source": row[4]}
        for row in rows if str(row[0]) not in excluded
    ]


def _copy_sqlite(source: Path, target: Path) -> None:
    """Create a consistent SQLite snapshot, including committed WAL pages."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError(f"refusing to overwrite SQLite snapshot: {target}")
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_conn:
        with sqlite3.connect(target) as target_conn:
            source_conn.backup(target_conn)
    _integrity(target)


def _remove_selected(cache_path: Path, selected_ids: Iterable[str]) -> None:
    ids = tuple(selected_ids)
    if not ids:
        raise ValueError("cannot prepare an empty selected cache")
    with sqlite3.connect(cache_path) as conn:
        columns = _table_columns(conn, "transcript_cache")
        if "video_id" not in columns:
            raise ValueError("staging cache lacks transcript_cache.video_id")
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM transcript_cache WHERE video_id IN ({placeholders})", ids)
        conn.commit()
    with _ro(cache_path) as conn:
        remaining = conn.execute(
            f"SELECT DISTINCT video_id FROM transcript_cache WHERE video_id IN ({placeholders})", ids
        ).fetchall()
    if remaining:
        raise ValueError(f"selected IDs remain in staging cache: {remaining[:5]}")
    _integrity(cache_path)


def _balanced(items: list[dict[str, Any]], items_per_account: int) -> dict[str, list[str]]:
    needed = len(ACCOUNT_ORDER) * items_per_account
    if len(items) != needed:
        raise ValueError(f"expected exactly {needed} candidates, found {len(items)}")
    result = {account: [] for account in ACCOUNT_ORDER}
    for index, row in enumerate(items):
        result[ACCOUNT_ORDER[index % len(ACCOUNT_ORDER)]].append(row["video_id"])
    return result


def load_account_settings_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    """Load optional per-account overrides without applying defaults."""
    if path is None:
        return {}
    try:
        raw = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load account settings: {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("account settings must be a JSON object keyed by account profile")
    return {str(account): dict(value) for account, value in raw.items()}


def validate_environment_overrides(
    overrides: Mapping[str, Mapping[str, str]] | None,
) -> dict[str, dict[str, str]]:
    """Validate arm-local environment without permitting ambient config keys."""
    if overrides is None:
        return {arm: {} for arm in ARMS}
    if not isinstance(overrides, Mapping):
        raise ValueError("environment_overrides must be an object keyed by arm")
    unknown_arms = set(overrides) - set(ARMS)
    if unknown_arms:
        raise ValueError("environment overrides contain unknown arms: " + ", ".join(sorted(map(str, unknown_arms))))
    result: dict[str, dict[str, str]] = {arm: {} for arm in ARMS}
    for arm, raw in overrides.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"environment overrides for {arm!r} must be an object")
        for key, value in raw.items():
            if not isinstance(key, str) or not _ENVIRONMENT_KEY.fullmatch(key):
                raise ValueError(f"invalid environment override key for {arm}: {key!r}")
            if not isinstance(value, str):
                raise ValueError(f"environment override {arm}/{key} must be a string")
            result[str(arm)][key] = value
    return result


def _command_template(arm: str, pair: str) -> list[str]:
    base = ["python", "scripts/run_multi_account_fetch.py", "--db-path", "{staging_db}",
            "--transcript-cache-db-path", "{staging_cache}", "--video-manifest", "{manifest}",
            "--limit", "{selected_count}", "--output-root", "{run_root}",
            "--parallel-accounts", "--workers-per-account", "{global_default_workers}",
            "--account-settings", "{account_settings}"]
    return base


def effective_account_settings(
    arm: str,
    *,
    batch_size: int | None = None,
    account_settings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    if batch_size is not None and (
        isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1
    ):
        raise ValueError("batch_size must be a positive integer or None")
    if account_settings is not None and not isinstance(account_settings, Mapping):
        raise ValueError("account_settings must be an object keyed by account profile")
    overrides = dict(account_settings or {})
    unknown_accounts = set(overrides) - set(ACCOUNT_ORDER)
    if unknown_accounts:
        raise ValueError(
            "account settings contain unknown account profiles: "
            + ", ".join(sorted(str(item) for item in unknown_accounts))
        )
    settings: dict[str, dict[str, Any]] = {}
    for account in ACCOUNT_ORDER:
        raw = overrides.get(account, {})
        if not isinstance(raw, Mapping):
            raise ValueError(f"account settings for {account!r} must be an object")
        unknown_keys = set(raw) - ACCOUNT_SETTING_KEYS
        if unknown_keys:
            raise ValueError(
                f"account settings for {account!r} contain unknown keys: "
                + ", ".join(sorted(str(item) for item in unknown_keys))
            )
        adaptive_default = arm == "adaptive" and ACCOUNTS[account]["billing_plan"] == "Pro"
        if arm == "control":
            effective = {
                "workers_per_account": 3,
                "batch_size": batch_size,
                "adaptive_workers": False,
                "adaptive_min_workers": 1,
                "adaptive_max_workers": None,
                "adaptive_scale_up_backlog": 2,
                "adaptive_scale_down_backlog": 0,
                "adaptive_cooldown_s": 60.0,
                "adaptive_health_window": 2,
            }
            effective.update({key: raw[key] for key in ("workers_per_account", "batch_size") if key in raw})
        else:
            effective = {
                "workers_per_account": 3,
                "batch_size": batch_size,
                "adaptive_workers": adaptive_default,
                "adaptive_min_workers": 1,
                "adaptive_max_workers": 5 if adaptive_default else None,
                "adaptive_scale_up_backlog": 2,
                "adaptive_scale_down_backlog": 0,
                "adaptive_cooldown_s": 60.0,
                "adaptive_health_window": 2,
            }
            effective.update(dict(raw))
        workers = effective["workers_per_account"]
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError(f"{account}.workers_per_account must be a positive integer")
        setting_batch = effective["batch_size"]
        if setting_batch is not None and (
            isinstance(setting_batch, bool) or not isinstance(setting_batch, int) or setting_batch < 1
        ):
            raise ValueError(f"{account}.batch_size must be a positive integer or None")
        if not isinstance(effective["adaptive_workers"], bool):
            raise ValueError(f"{account}.adaptive_workers must be boolean")
        if not effective["adaptive_workers"]:
            if arm == "adaptive" and "adaptive_max_workers" in raw and raw["adaptive_max_workers"] is not None:
                raise ValueError(f"{account}.adaptive_max_workers requires adaptive_workers")
            effective.update({
                "adaptive_min_workers": 1,
                "adaptive_max_workers": None,
                "adaptive_scale_up_backlog": 2,
                "adaptive_scale_down_backlog": 0,
                "adaptive_cooldown_s": 60.0,
                "adaptive_health_window": 2,
            })
        else:
            minimum = effective["adaptive_min_workers"]
            maximum = effective["adaptive_max_workers"]
            if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= workers:
                raise ValueError(f"{account}.adaptive_min_workers must be between 1 and workers_per_account")
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < workers:
                raise ValueError(f"{account}.adaptive_max_workers must be >= workers_per_account")
            for field in ("adaptive_scale_up_backlog", "adaptive_scale_down_backlog", "adaptive_health_window"):
                value = effective[field]
                minimum_value = 1 if field == "adaptive_health_window" else 0
                if isinstance(value, bool) or not isinstance(value, int) or value < minimum_value:
                    raise ValueError(f"{account}.{field} is invalid")
            cooldown = effective["adaptive_cooldown_s"]
            if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or cooldown < 0:
                raise ValueError(f"{account}.adaptive_cooldown_s is invalid")
        settings[account] = effective
    return settings


def adaptive_workload_requirements(
    settings: Mapping[str, Any],
    *,
    items_per_account: int,
    default_batch_size: int = DEFAULT_NLM_BATCH_SIZE,
    industrial_batches_per_worker: int = DEFAULT_INDUSTRIAL_BATCHES_PER_WORKER,
) -> dict[str, Any]:
    """Return the conservative workload floor for an observable scale-up.

    The runtime can consume up to ``industrial_batches_per_worker`` logical
    batches per active slot before the next scheduler observation. The
    scheduler also needs its configured health window and scale-up backlog.
    This is only a packet-feasibility floor; successful scale-up still depends
    on live worker health and timing.
    """
    if items_per_account < 1:
        raise ValueError("items_per_account must be >= 1")
    if default_batch_size < 1 or industrial_batches_per_worker < 1:
        raise ValueError("adaptive workload defaults must be positive")
    initial_workers = settings.get("workers_per_account")
    maximum_workers = settings.get("adaptive_max_workers")
    if not isinstance(initial_workers, int) or isinstance(initial_workers, bool) or initial_workers < 1:
        raise ValueError("adaptive workload workers_per_account is invalid")
    if not isinstance(maximum_workers, int) or isinstance(maximum_workers, bool):
        raise ValueError("adaptive workload adaptive_max_workers is invalid")
    if maximum_workers <= initial_workers:
        raise ValueError(
            "adaptive workload cannot scale: adaptive_max_workers must exceed "
            f"workers_per_account ({maximum_workers} <= {initial_workers})"
        )
    scale_up_backlog = settings.get("adaptive_scale_up_backlog")
    health_window = settings.get("adaptive_health_window")
    if not isinstance(scale_up_backlog, int) or isinstance(scale_up_backlog, bool) or scale_up_backlog < 0:
        raise ValueError("adaptive workload adaptive_scale_up_backlog is invalid")
    if not isinstance(health_window, int) or isinstance(health_window, bool) or health_window < 1:
        raise ValueError("adaptive workload adaptive_health_window is invalid")
    configured_batch_size = settings.get("batch_size")
    batch_size = default_batch_size if configured_batch_size is None else configured_batch_size
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError("adaptive workload batch_size is invalid")
    required_batches = max(
        1,
        (initial_workers + health_window - 1) * industrial_batches_per_worker
        + scale_up_backlog,
    )
    logical_batches = math.ceil(items_per_account / batch_size)
    required_items = (required_batches - 1) * batch_size + 1
    return {
        "batch_size": batch_size,
        "industrial_batches_per_worker": industrial_batches_per_worker,
        "initial_workers": initial_workers,
        "max_workers": maximum_workers,
        "health_window": health_window,
        "scale_up_backlog": scale_up_backlog,
        "required_logical_batches": required_batches,
        "logical_batches": logical_batches,
        "required_items_per_account": required_items,
        "feasible": logical_batches >= required_batches,
    }


def prepare_throughput_pair(
    *, db_path: Path, reference_cache_path: Path, output_root: Path,
    items_per_account: int = 2, require_adaptive_scale_up: bool = True,
    require_adaptive_workload: bool = False,
    caption_state: str = "captioned",
    batch_size: int | None = None,
    account_settings: Mapping[str, Mapping[str, Any]] | None = None,
    exclude_video_ids: Iterable[str] = (),
    comparison_mode: str = "adaptive",
    environment_overrides: Mapping[str, Mapping[str, str]] | None = None,
    abort_on_source_add_failure: bool = False,
) -> dict[str, Any]:
    """Build two disjoint control/adaptive pairs and isolated staging copies."""
    db_path, reference_cache_path, output_root = map(Path, (db_path, reference_cache_path, output_root))
    if items_per_account < 1:
        raise ValueError("items_per_account must be >= 1")
    if comparison_mode not in COMPARISON_MODES:
        raise ValueError(f"unsupported comparison_mode: {comparison_mode}")
    environment_overrides = validate_environment_overrides(environment_overrides)
    if batch_size is not None and (
        isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1
    ):
        raise ValueError("batch_size must be a positive integer or None")
    _caption_predicate(caption_state)
    excluded_video_ids = sorted({str(video_id) for video_id in exclude_video_ids if str(video_id)})
    if db_path.resolve() == reference_cache_path.resolve():
        raise ValueError("canonical batch DB and reference cache must be distinct")
    _integrity(db_path)
    _integrity(reference_cache_path)
    candidates = _read_candidates(
        db_path, caption_state=caption_state,
        exclude_video_ids=excluded_video_ids,
    )
    per_pair = len(ACCOUNT_ORDER) * items_per_account
    needed = per_pair * len(PAIRS)
    if len(candidates) < needed:
        raise ValueError(f"insufficient deterministic cohort: need {needed}, found {len(candidates)}")
    selected = candidates[:needed]
    pairs: dict[str, Any] = {}
    for offset, pair in enumerate(PAIRS):
        rows = selected[offset * per_pair:(offset + 1) * per_pair]
        manifests = _balanced(rows, items_per_account)
        cohort_ids = [row["video_id"] for row in rows]
        pair_payload: dict[str, Any] = {
            "pair_id": pair,
            "cohort_ids": cohort_ids,
            "cohort_fingerprint": fingerprint(cohort_ids),
            "account_manifests": manifests,
            "account_manifest_fingerprint": fingerprint(manifests),
            "arms": {},
        }
        arm_preflight: dict[str, tuple[dict[str, dict[str, Any]], list[str], dict[str, Any]]] = {}
        for arm in ARMS:
            settings_arm = "control" if comparison_mode == "environment" else arm
            effective_settings = effective_account_settings(
                settings_arm,
                batch_size=batch_size,
                account_settings=account_settings,
            )
            adaptive_targets = [
                account for account, settings in effective_settings.items()
                if settings.get("adaptive_workers") is True
            ]
            if comparison_mode != "environment" and arm == "adaptive" and require_adaptive_scale_up and not adaptive_targets:
                raise ValueError("adaptive arm has no adaptive account targets")
            adaptive_workload: dict[str, Any] = {}
            if comparison_mode != "environment" and arm == "adaptive" and require_adaptive_workload:
                for account in adaptive_targets:
                    requirement = adaptive_workload_requirements(
                        effective_settings[account],
                        items_per_account=items_per_account,
                    )
                    adaptive_workload[account] = requirement
                    if not requirement["feasible"]:
                        raise ValueError(
                            "adaptive workload is too small for "
                            f"{account}: need at least {requirement['required_items_per_account']} "
                            f"items/account ({requirement['required_logical_batches']} logical batches), "
                            f"planned {items_per_account} items/account ({requirement['logical_batches']} batches); "
                            f"effective batch_size={requirement['batch_size']}"
                        )
            arm_preflight[arm] = (effective_settings, adaptive_targets, adaptive_workload)
        for arm in ARMS:
            effective_settings, adaptive_targets, adaptive_workload = arm_preflight[arm]
            stage = output_root / pair / arm
            stage.mkdir(parents=True, exist_ok=False)
            stage_db, stage_cache = stage / "batch_status.sqlite", stage / "transcripts.sqlite"
            _copy_sqlite(db_path, stage_db)
            _copy_sqlite(reference_cache_path, stage_cache)
            _remove_selected(stage_cache, cohort_ids)
            account_settings_path = stage / "account-settings.json"
            account_settings_path.write_text(
                json.dumps(effective_settings, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest_templates = {}
            for account, ids in manifests.items():
                manifest_path = stage / f"manifest-{account.replace('.', '-')}.json"
                manifest_payload = {
                    "manifest_version": 1, "pair_id": pair, "arm": arm,
                    "account_profile": account, "video_ids": ids,
                    "video_ids_fingerprint": fingerprint(ids),
                    "cohort_fingerprint": fingerprint(cohort_ids),
                }
                manifest_path.write_text(
                    json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                manifest_templates[account] = {
                    **manifest_payload, "manifest_path": str(manifest_path.resolve())
                }
            pair_payload["arms"][arm] = {
                "settings": {
                    "worker_policy": "per_account_settings",
                    "adaptive_target_accounts": adaptive_targets,
                    "require_target_workers_gt": {
                        account: effective_settings[account]["workers_per_account"]
                        for account in adaptive_targets
                    } if comparison_mode != "environment" and arm == "adaptive" and require_adaptive_scale_up else {},
                    "adaptive_workload": adaptive_workload,
                    "fallbacks_enabled": False, "auth_preflight": "required_by_runner",
                },
                "staging_db": str(stage_db.resolve()), "staging_cache": str(stage_cache.resolve()),
                "account_settings_path": str(account_settings_path.resolve()),
                "account_settings_fingerprint": file_fingerprint(account_settings_path),
                "selected_cache_absent_before_launch": True,
                "db_integrity": "ok", "cache_integrity": "ok",
                "command_template": _command_template(arm, pair),
                "manifest_templates": manifest_templates,
                "effective_account_settings": effective_settings,
                "effective_settings_fingerprint": fingerprint(effective_settings),
                "effective_settings_fingerprints": {
                    account: fingerprint(settings)
                    for account, settings in effective_settings.items()
                },
                "environment_overrides": dict(environment_overrides.get(arm, {})),
                "environment_overrides_fingerprint": fingerprint(environment_overrides.get(arm, {})),
                "abort_on_source_add_failure": bool(abort_on_source_add_failure),
                "artifact_fingerprints": {
                    "staging_db": file_fingerprint(stage_db),
                    "staging_cache": file_fingerprint(stage_cache),
                    "manifests": {
                        account: file_fingerprint(Path(data["manifest_path"]))
                        for account, data in manifest_templates.items()
                    },
                },
            }
        pairs[pair] = pair_payload
    packet = {
        "packet_version": 2, "kind": "offline_uncached_throughput_pair",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "packet_root": str(output_root.resolve()),
        "canonical_db": str(db_path.resolve()), "reference_cache": str(reference_cache_path.resolve()),
        "canonical_fingerprints": {"db": file_fingerprint(db_path), "reference_cache": file_fingerprint(reference_cache_path)},
        "exclusions": {
            "video_ids": excluded_video_ids,
            "fingerprint": fingerprint(excluded_video_ids),
            "reason": EXCLUSION_REASON,
            "scope": "offline_candidate_selection_only",
        },
        "accounts": {account: dict(value) for account, value in ACCOUNTS.items()},
        "cohort": {"pair_count": 2, "items_per_account": items_per_account, "total_ids": needed,
                   "caption_state": caption_state,
                   "batch_size": batch_size,
                   "candidate_selection": "authoritative_pending_rows_then_remove_selected_ids_from_staging_cache",
                   "pair_ids": list(PAIRS), "cohort_fingerprint": fingerprint([row["video_id"] for row in selected])},
        "account_settings_overrides": dict(account_settings or {}),
        "account_settings_overrides_fingerprint": fingerprint(dict(account_settings or {})),
        "comparison_mode": comparison_mode,
        "environment_overrides": environment_overrides,
        "environment_overrides_fingerprint": fingerprint(environment_overrides),
        "pairs": pairs,
        "live_launch": False,
        "exact_commands": {f"{pair}/{arm}": _command_template(arm, pair) for pair in PAIRS for arm in ARMS},
    }
    output_root.mkdir(parents=True, exist_ok=True) if not output_root.exists() else None
    packet_path = output_root / "throughput_pair_packet.json"
    packet["packet_path"] = str(packet_path.resolve())
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return packet


def _load(value: Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return json.loads(Path(value).read_text(encoding="utf-8"))


def validate_receipts(packet: Path | Mapping[str, Any], receipts: Iterable[Path | Mapping[str, Any]]) -> dict[str, Any]:
    """Read-only reconcile completed arm receipts against a preparation packet."""
    packet_data = _load(packet)
    receipt_data = [_load(item) for item in receipts]
    issues: list[str] = []
    expected = {(pair, arm): packet_data["pairs"][pair] for pair in PAIRS for arm in ARMS}
    seen: set[tuple[str, str]] = set()
    for receipt in receipt_data:
        key = (str(receipt.get("pair_id")), str(receipt.get("arm")))
        if key not in expected:
            issues.append(f"unexpected_receipt:{key}")
            continue
        if key in seen:
            issues.append(f"duplicate_receipt:{key}")
        seen.add(key)
        pair = expected[key]
        expected_ids = list(pair["cohort_ids"])
        actual_ids = receipt.get("selected_ids")
        if actual_ids != expected_ids:
            issues.append(f"cohort_mismatch:{key}")
        if len(actual_ids or []) != len(set(actual_ids or [])):
            issues.append(f"duplicate_selected_ids:{key}")
        if receipt.get("selected_cache_absent_before_launch") is not True:
            issues.append(f"selected_cache_not_absent_before_launch:{key}")
        for field in ("db_integrity", "cache_integrity"):
            if str(receipt.get(field, "")).lower() != "ok":
                issues.append(f"{field}_failed:{key}")
        _validate_db_receipt(receipt, expected_ids, issues, key)
        _validate_cache_receipt(receipt, expected_ids, issues, key)
        outcomes = receipt.get("outcomes")
        if not isinstance(outcomes, list):
            issues.append(f"outcomes_missing:{key}")
            continue
        outcome_ids = [str(row.get("video_id")) for row in outcomes if isinstance(row, Mapping)]
        if outcome_ids != expected_ids or len(outcome_ids) != len(set(outcome_ids)):
            issues.append(f"outcome_ids_missing_or_duplicate:{key}")
        for row in outcomes:
            if not isinstance(row, Mapping):
                issues.append(f"invalid_outcome:{key}")
                continue
            text = json.dumps(row, sort_keys=True).lower()
            if any(token in text for token in FORBIDDEN_TEXT) or row.get("fallback_used") or row.get("auth_failed"):
                issues.append(f"forbidden_fallback_or_auth_failure:{key}:{row.get('video_id')}")
            if row.get("status") == "complete" and not row.get("cache_non_empty"):
                issues.append(f"success_cache_empty:{key}:{row.get('video_id')}")
        if key[1] == "adaptive":
            arm_data = pair.get("arms", {}).get("adaptive") if isinstance(pair.get("arms"), Mapping) else None
            settings = arm_data.get("effective_account_settings") if isinstance(arm_data, Mapping) else None
            settings = settings if isinstance(settings, Mapping) else effective_account_settings("adaptive")
            target_workers = receipt.get("target_workers")
            target_workers = target_workers if isinstance(target_workers, Mapping) else {}
            adaptive_targets = {
                str(account): int(value["workers_per_account"])
                for account, value in settings.items()
                if isinstance(value, Mapping)
                and value.get("adaptive_workers") is True
                and isinstance(value.get("workers_per_account"), int)
                and not isinstance(value.get("workers_per_account"), bool)
            }
            if not adaptive_targets:
                issues.append(f"adaptive_no_target_accounts:{key}")
            for account, initial_workers in adaptive_targets.items():
                observed = target_workers.get(account)
                if not isinstance(observed, list) or not any(
                    isinstance(value, int) and not isinstance(value, bool) and value > initial_workers
                    for value in observed
                ):
                    label = "pro" if account == "a.hominidae" else account
                    issues.append(f"adaptive_{label}_did_not_scale:{key}")
    missing_receipts = sorted(set(expected) - seen)
    issues.extend(f"missing_receipt:{key}" for key in missing_receipts)
    return {"status": "passed" if not issues else "failed", "issues": issues,
            "validated_receipt_count": len(seen), "packet_cohort_fingerprint": packet_data["cohort"]["cohort_fingerprint"]}


def _validate_db_receipt(receipt: Mapping[str, Any], ids: list[str], issues: list[str], key: tuple[str, str]) -> None:
    db_path = receipt.get("staging_db")
    if not db_path:
        issues.append(f"staging_db_missing:{key}")
        return
    try:
        if _integrity(Path(str(db_path))) != "ok":
            issues.append(f"db_integrity_failed:{key}")
        with _ro(Path(str(db_path))) as conn:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(f"SELECT video_id, status FROM analysis_status WHERE video_id IN ({placeholders})", ids).fetchall()
        by_id = Counter(str(row[0]) for row in rows)
        if set(by_id) != set(ids) or any(count != 1 for count in by_id.values()):
            issues.append(f"db_missing_or_duplicate_selected_ids:{key}")
        if any(str(row[1]) == "pending" for row in rows):
            issues.append(f"selected_ids_still_pending:{key}")
    except (OSError, sqlite3.Error, ValueError) as exc:
        issues.append(f"db_read_failed:{key}:{type(exc).__name__}")


def _validate_cache_receipt(receipt: Mapping[str, Any], ids: list[str], issues: list[str], key: tuple[str, str]) -> None:
    cache_path = receipt.get("staging_cache")
    if not cache_path:
        issues.append(f"staging_cache_missing:{key}")
        return
    try:
        if _integrity(Path(str(cache_path))) != "ok":
            issues.append(f"cache_integrity_failed:{key}")
        with _ro(Path(str(cache_path))) as conn:
            columns = _table_columns(conn, "transcript_cache")
            if "video_id" not in columns or "transcript" not in columns:
                issues.append(f"cache_schema_invalid:{key}")
                return
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT video_id, transcript FROM transcript_cache WHERE video_id IN ({placeholders})", ids
            ).fetchall()
        non_empty = {str(row[0]) for row in rows if row[1] is not None and str(row[1]).strip()}
        for outcome in receipt.get("outcomes", []):
            if isinstance(outcome, Mapping) and outcome.get("status") == "complete":
                video_id = str(outcome.get("video_id"))
                if video_id not in non_empty:
                    issues.append(f"success_cache_empty:{key}:{video_id}")
    except (OSError, sqlite3.Error, ValueError) as exc:
        issues.append(f"cache_read_failed:{key}:{type(exc).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser(
        "prepare",
        help="create isolated staging artifacts only; this command never launches workers",
    )
    prep.add_argument("--db", type=Path, required=True)
    prep.add_argument("--reference-cache", type=Path, required=True)
    prep.add_argument("--output-root", type=Path, required=True)
    prep.add_argument("--items-per-account", type=int, default=2)
    prep.add_argument("--batch-size", type=int, default=None)
    prep.add_argument("--caption-state", choices=CAPTION_STATES, default="captioned")
    prep.add_argument("--account-settings-json", type=Path, default=None,
                      help="Optional JSON object of per-account settings overrides")
    prep.add_argument("--comparison-mode", choices=COMPARISON_MODES, default="adaptive")
    prep.add_argument("--environment-overrides-json", type=Path, default=None,
                      help="Optional JSON object keyed by arm with YTIS_* environment overrides")
    prep.add_argument("--abort-on-source-add-failure", action="store_true")
    prep.add_argument("--exclude-video-id", action="append", default=[],
                      help="Offline benchmark-only candidate exclusion; may be repeated")
    val = sub.add_parser("validate")
    val.add_argument("--packet", type=Path, required=True)
    val.add_argument("--receipt", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_throughput_pair(
            db_path=args.db,
            reference_cache_path=args.reference_cache,
            output_root=args.output_root,
            items_per_account=args.items_per_account,
            caption_state=args.caption_state,
            batch_size=args.batch_size,
            account_settings=load_account_settings_overrides(args.account_settings_json)
            if args.command == "prepare" else None,
            exclude_video_ids=args.exclude_video_id,
            comparison_mode=args.comparison_mode,
            environment_overrides=json.loads(args.environment_overrides_json.read_text(encoding="utf-8"))
            if args.command == "prepare" and args.environment_overrides_json else None,
            abort_on_source_add_failure=args.abort_on_source_add_failure if args.command == "prepare" else False,
        ) if args.command == "prepare" \
            else validate_receipts(args.packet, args.receipt)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status", "passed") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
