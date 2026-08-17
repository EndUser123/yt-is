"""Authoritative evidence drill-down for one video in one chunk/account.

Chain (decision packet §B.4, demonstrated end-to-end for both failure
classes): chunk summary -> account result -> manifest entry (+fingerprint)
-> per-video attempt events (action/timestamp/attempt/elapsed/error) ->
authoritative analysis_status row -> transcript cache row with quality
metadata. Every step names the exact artifact path it came from.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import core


def drill(
    ctx: core.MonitorContext,
    *,
    chunk: int | str,
    account: str | None = None,
    video_id: str | None = None,
) -> dict:
    records = ctx.chunk_records()
    record = None
    if isinstance(chunk, int):
        for candidate in records:
            if candidate.index == chunk:
                record = candidate
                break
    if record is None:
        # Also allow "chunk-0004" style or a direct run-root path.
        text = str(chunk)
        root = None
        if record is None and text.isdigit():
            for candidate in records:
                if candidate.index == int(text):
                    record = candidate
                    break
        if record is None:
            maybe = Path(text)
            if maybe.is_dir():
                root = maybe
        if root is None and record is None:
            return {"error": "chunk_not_found", "chunk": chunk}
        if root is not None:
            record = core.ChunkRecord(
                index=-1,
                status=None,
                selected_count=None,
                selected_complete_count=None,
                output_root=str(root),
                summary_path=str(root / "multi_account_fetch_summary.json"),
                returncode=None,
            )
    chunk_root = Path(record.output_root) if record.output_root else None
    if chunk_root is None or not chunk_root.is_dir():
        return {
            "error": "chunk_evidence_swept_or_missing",
            "chunk": record.index,
            "output_root": record.output_root,
            "note": "run-dir artifacts are swept ~7 days after going quiet; "
            "DB-level evidence remains permanently",
        }

    summary, summary_err = core.load_summary(record.summary_path)
    out: dict = {
        "chunk": record.index,
        "chunk_root": str(chunk_root),
        "summary_path": record.summary_path,
        "summary_status": (summary or {}).get("status"),
        "run_id": (summary or {}).get("run_id"),
        "account": account,
        "video_id": video_id,
    }
    if summary is None:
        out["summary_error"] = summary_err

    # account scoping
    accounts: list[str] = []
    if summary:
        for raw in summary.get("account_results") or []:
            if isinstance(raw, dict) and isinstance(raw.get("account_profile"), str):
                accounts.append(raw["account_profile"])
    if not accounts:
        accounts = _discover(chunk_root)
    if account is None and len(accounts) == 1:
        account = accounts[0]
    out["account"] = account
    if account and account not in accounts and accounts:
        out["account_not_in_chunk"] = accounts

    # manifest + receipt provenance
    if account and summary:
        result = core.account_result(summary, account)
        if result:
            out["account_result"] = {
                key: result.get(key)
                for key in (
                    "account_profile",
                    "status",
                    "video_count",
                    "selected_complete_count",
                    "elapsed_s",
                    "manifest_path",
                    "receipt_path",
                    "event_log_dir",
                )
            }
            manifest_path = result.get("manifest_path")
            if isinstance(manifest_path, str):
                from csf.video_selection_manifest import load_video_selection_manifest

                try:
                    manifest = load_video_selection_manifest(Path(manifest_path))
                    entry = next(
                        (
                            item
                            for item in manifest.items
                            if item.video_id == video_id
                        ),
                        None,
                    )
                    out["manifest"] = {
                        "path": manifest_path,
                        "selection_name": manifest.selection_name,
                        "input_database_fingerprint": getattr(
                            manifest, "input_database_fingerprint", None
                        ),
                        "video_entry": (
                            {"video_id": entry.video_id} if entry else None
                        ),
                    }
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    out["manifest"] = {
                        "path": manifest_path,
                        "error": f"unreadable:{type(exc).__name__}",
                    }
            receipt_path = result.get("receipt_path")
            if isinstance(receipt_path, str):
                receipt, _ = core._read_json(Path(receipt_path))
                out["receipt"] = {
                    "path": receipt_path,
                    "available": isinstance(receipt, dict),
                }

    # per-video events
    if account and video_id:
        events = _video_events(chunk_root, account, video_id)
        out["events"] = events
        out["event_count"] = len(events)

    # authoritative DB row + cache
    if video_id:
        row = ctx.analysis_rows([video_id]).get(video_id)
        out["analysis_status_row"] = row if row else {"found": False}
        cache = ctx.cache_row(video_id)
        out["transcript_cache_row"] = cache

    out["trail"] = [
        "chunk summary -> account result -> manifest entry (fingerprint) -> "
        "per-video events -> analysis_status row -> transcript_cache row"
    ]
    return out


def _video_events(chunk_root: Path, account: str, video_id: str) -> list[dict]:
    collected: list[dict] = []
    for path in core.event_files_for_account(chunk_root, account):
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or video_id not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                collected.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "action": event.get("action"),
                        "trace_id": event.get("trace_id"),
                        "run_id": data.get("run_id"),
                        "account_profile": data.get("account_profile"),
                        "worker_id": data.get("worker_id"),
                        "video_id": data.get("video_id"),
                        "source_id": data.get("source_id"),
                        "attempt": data.get("attempt") or data.get("attempts"),
                        "elapsed_s": data.get("elapsed_s"),
                        "status": data.get("status"),
                        "error": data.get("error"),
                        "reason": data.get("reason"),
                        "source_file": str(path),
                    }
                )
    collected.sort(key=lambda e: e.get("timestamp") or "")
    return collected


def _discover(chunk_root: Path) -> list[str]:
    accounts_dir = chunk_root / "accounts"
    if not accounts_dir.is_dir():
        return []
    return sorted(child.name.replace("-", ".") for child in accounts_dir.iterdir() if child.is_dir())
