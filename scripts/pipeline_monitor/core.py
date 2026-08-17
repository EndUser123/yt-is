"""Read-only artifact loaders for the yt-is operational monitor.

Every loader in this module is strictly read-only: SQLite is opened with
``mode=ro`` URIs, JSON artifacts are parsed from disk, and nothing is ever
written. Missing or swept artifacts are returned as ``None`` together with a
reason so callers can classify evidence as UNKNOWN_STALE instead of failing.

Provenance rule (observability contract checklist): every value the monitor
emits names the artifact it came from. Loaders therefore return the source
path alongside parsed content wherever practical.

Evidence sources covered (decision packet 2026-08-17 §A):
  - unattended supervisor state.json
  - per-chunk supervisor_runtime.json
  - per-chunk multi_account_fetch_summary.json (coordinator)
  - manifests/<account>.json + receipts/<account>.json
  - accounts/<account>/events/*.jsonl (structured event envelope)
  - batch_status.sqlite analysis_status (authoritative video state)
  - transcripts.sqlite transcript_cache
  - nlm-auth keepalive log
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_STATE_PATH = Path("P:/.data/yt-is/unattended-backlog/state.json")
DEFAULT_KEEPALIVE_LOG = Path("P:/.data/yt-is/nlm-auth/keepalive.log")

# Freshness anchors (decision packet §E). The supervisor refreshes
# heartbeat_at_epoch every 30s; 90s allows three missed beats before the
# monitor calls the evidence stale.
HEARTBEAT_FRESH_S = 90.0
# No forward progress for this long while state=running => stalled candidate.
PROGRESS_WINDOW_S = 30 * 60.0


def _read_json(path: Path) -> tuple[dict | list | None, str | None]:
    if not path.is_file():
        return None, "missing"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable:{type(exc).__name__}"
    return value, None


def _connect_ro(db_path: Path) -> tuple[sqlite3.Connection | None, str | None]:
    if not db_path.is_file():
        return None, "missing"
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True, timeout=5.0), None
    except sqlite3.Error as exc:
        return None, f"unreadable:{type(exc).__name__}"


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_naive_local(value: object) -> datetime | None:
    """Parse naive-local timestamps (transcript_cache.cached_at contract).

    The cache writer stamps ``datetime.now().isoformat()`` — naive local
    time. Assuming UTC here would inflate ages by the local UTC offset and
    make fresh evidence look stale (the exact trap this replaces).
    ``astimezone()`` on a naive datetime attaches the local zone.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def age_s(moment: datetime | None) -> float | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())


@dataclass
class ChunkRecord:
    """One chunk from the supervisor state, enriched on demand."""

    index: int
    status: str | None
    selected_count: int | None
    selected_complete_count: int | None
    output_root: str | None
    summary_path: str | None
    returncode: int | None
    failure_type: str | None = None
    failure_stage: str | None = None
    failure_reason: str | None = None
    terminalized_failures: bool | None = None

    @property
    def executed(self) -> bool:
        # The supervisor leaves never-run chunk records as status="planned"
        # with zero completions (state.json quirk noted in the decision
        # packet self-review). They carry no outcome evidence.
        return not (self.status == "planned" and not self.selected_complete_count)


@dataclass
class MonitorContext:
    """Resolved paths + loaded state shared by all views."""

    state_path: Path
    db_path: Path
    transcript_db_path: Path
    keepalive_log: Path
    state: dict | None = None
    state_error: str | None = None
    db_error: str | None = None

    @classmethod
    def create(
        cls,
        *,
        state_path: Path | None = None,
        db_path: Path | None = None,
        transcript_db_path: Path | None = None,
        keepalive_log: Path | None = None,
        load_env: bool = True,
    ) -> "MonitorContext":
        if load_env:
            from csf.paths import load_workspace_env

            load_workspace_env()
        from csf.paths import get_batch_db_path, get_transcript_db_path

        state, err = _read_json(state_path or DEFAULT_STATE_PATH)
        return cls(
            state_path=state_path or DEFAULT_STATE_PATH,
            db_path=db_path or get_batch_db_path(),
            transcript_db_path=transcript_db_path or get_transcript_db_path(),
            keepalive_log=keepalive_log or DEFAULT_KEEPALIVE_LOG,
            state=state if isinstance(state, dict) else None,
            state_error=err,
        )

    # -- state accessors -------------------------------------------------

    @property
    def supervisor_status(self) -> str | None:
        if not self.state:
            return None
        value = self.state.get("status")
        return value if isinstance(value, str) else None

    @property
    def state_updated_at(self) -> datetime | None:
        if not self.state:
            return None
        return _parse_iso(self.state.get("updated_at"))

    def chunk_records(self) -> list[ChunkRecord]:
        if not self.state:
            return []
        raw = self.state.get("chunks")
        if not isinstance(raw, list):
            return []
        records: list[ChunkRecord] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            selected = item.get("selected_count")
            complete = item.get("selected_complete_count")
            records.append(
                ChunkRecord(
                    index=item.get("index") if isinstance(item.get("index"), int) else len(records),
                    status=item.get("status") if isinstance(item.get("status"), str) else None,
                    selected_count=selected if isinstance(selected, int) else None,
                    selected_complete_count=complete if isinstance(complete, int) else None,
                    output_root=item.get("output_root") if isinstance(item.get("output_root"), str) else None,
                    summary_path=item.get("summary_path") if isinstance(item.get("summary_path"), str) else None,
                    returncode=item.get("returncode") if isinstance(item.get("returncode"), int) else None,
                    failure_type=item.get("failure_type") if isinstance(item.get("failure_type"), str) else None,
                    failure_stage=item.get("failure_stage") if isinstance(item.get("failure_stage"), str) else None,
                    failure_reason=item.get("failure_reason") if isinstance(item.get("failure_reason"), str) else None,
                    terminalized_failures=item.get("terminalized_failures")
                    if isinstance(item.get("terminalized_failures"), bool)
                    else None,
                )
            )
        return records

    def latest_chunk(self) -> ChunkRecord | None:
        records = self.chunk_records()
        return records[-1] if records else None

    def current_chunk(self) -> ChunkRecord | None:
        """Latest chunk record with an outcome or an in-flight runtime."""
        records = self.chunk_records()
        for record in reversed(records):
            if record.executed or record.status in {"launching", "recovering"}:
                return record
        return records[-1] if records else None

    # -- db helpers -------------------------------------------------------

    def backlog_counts(self) -> dict[str, int | None] | None:
        conn, err = _connect_ro(self.db_path)
        if conn is None:
            self.db_error = err
            return None
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM analysis_status GROUP BY status"
            ).fetchall()
        except sqlite3.Error as exc:
            self.db_error = f"unreadable:{type(exc).__name__}"
            conn.close()
            return None
        conn.close()
        counts = {str(status): int(count) for status, count in rows}
        return {
            "pending": counts.get("pending", 0),
            "complete": counts.get("complete", 0),
            "failed": counts.get("failed", 0),
        }

    def analysis_rows(self, video_ids: list[str]) -> dict[str, dict]:
        """Authoritative analysis_status rows for exact video IDs."""
        rows: dict[str, dict] = {}
        if not video_ids:
            return rows
        conn, err = _connect_ro(self.db_path)
        if conn is None:
            self.db_error = err
            return rows
        try:
            for offset in range(0, len(video_ids), 900):
                batch = video_ids[offset : offset + 900]
                placeholders = ",".join("?" for _ in batch)
                for row in conn.execute(
                    "SELECT video_id, status, last_stage, failure_reason, "
                    f"has_captions, updated_at, source FROM analysis_status "
                    f"WHERE video_id IN ({placeholders})",
                    batch,
                ):
                    rows[str(row[0])] = {
                        "video_id": str(row[0]),
                        "status": str(row[1]),
                        "last_stage": str(row[2]) if row[2] is not None else None,
                        "failure_reason": str(row[3]) if row[3] is not None else None,
                        "has_captions": int(row[4]) if row[4] is not None else None,
                        "updated_at": str(row[5]) if row[5] is not None else None,
                        "source": str(row[6]) if row[6] is not None else None,
                    }
        except sqlite3.Error as exc:
            self.db_error = f"unreadable:{type(exc).__name__}"
        finally:
            conn.close()
        return rows

    def cache_row(self, video_id: str) -> dict | None:
        conn, err = _connect_ro(self.transcript_db_path)
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT source, cached_at, metadata_json, LENGTH(transcript) "
                "FROM transcript_cache WHERE video_id = ? ORDER BY cached_at DESC LIMIT 1",
                (video_id,),
            ).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            conn.close()
        if not row:
            return None
        try:
            metadata = json.loads(row[2]) if row[2] else {}
        except json.JSONDecodeError:
            metadata = {"_unparseable": True}
        return {
            "video_id": video_id,
            "source": row[0],
            "cached_at": row[1],
            "transcript_chars": row[3],
            "metadata": metadata,
        }

    def latest_transcript_cached_at(self) -> datetime | None:
        conn, err = _connect_ro(self.transcript_db_path)
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT MAX(cached_at) FROM transcript_cache").fetchone()
        except sqlite3.Error:
            row = None
        finally:
            conn.close()
        return _parse_naive_local(row[0]) if row and row[0] else None


# -- chunk artifact loaders -------------------------------------------------


def load_runtime_receipt(output_root: Path | None) -> tuple[dict | None, str | None]:
    if output_root is None:
        return None, "no_output_root"
    return _read_json(output_root / "supervisor_runtime.json")


def load_summary(summary_path: str | None) -> tuple[dict | None, str | None]:
    if not summary_path:
        return None, "no_summary_path"
    value, err = _read_json(Path(summary_path))
    if value is not None and not isinstance(value, dict):
        return None, "not_object"
    return value, err


def account_result(summary: dict, account: str) -> dict | None:
    for raw in summary.get("account_results") or []:
        if isinstance(raw, dict) and raw.get("account_profile") == account:
            return raw
    return None


def event_files_for_account(chunk_root: Path, account: str) -> list[Path]:
    slug = account.replace(".", "-")
    base = chunk_root / "accounts"
    for candidate in (base / slug, base / account):
        events = candidate / "events"
        if events.is_dir():
            return sorted(events.glob("*.jsonl"))
    return []


@dataclass
class EventScan:
    """Reduced per-account event statistics for one chunk.

    The reduction covers exactly the stage/degradation signals demonstrated
    in decision packet §B.3: source-add attempt latency distribution, RPC9
    counts, materialization-wait latency, content-fetch latency and retries,
    sub-batch elapsed, per-worker last-event timestamps, and per-video
    attempt counts for retry-recovery joins.
    """

    account: str
    source_dir: str | None = None
    event_count: int = 0
    parse_errors: int = 0
    add_elapsed: list[float] = field(default_factory=list)
    rpc9_add_errors: int = 0
    wait_elapsed: list[float] = field(default_factory=list)
    fetch_elapsed: list[float] = field(default_factory=list)
    fetch_retries: int = 0
    subbatch_elapsed: list[float] = field(default_factory=list)
    materialization_terminal: int = 0
    retry_skipped_rpc9: int = 0
    worker_last_event: dict[str, str] = field(default_factory=dict)
    add_attempts_by_video: dict[str, int] = field(default_factory=dict)
    fetch_attempts_by_video: dict[str, int] = field(default_factory=dict)
    last_event_at: str | None = None
    auth_failures: int = 0
    notebook_cleanup_receipts: list[dict] = field(default_factory=list)
    retry_queue_wait_s: list[float] = field(default_factory=list)

    def consume(self, event: dict) -> None:
        action = event.get("action")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        timestamp = event.get("timestamp") if isinstance(event.get("timestamp"), str) else None
        if timestamp:
            self.last_event_at = timestamp
        worker = data.get("worker_id") if isinstance(data.get("worker_id"), str) else None
        if worker and timestamp:
            prior = self.worker_last_event.get(worker)
            if prior is None or timestamp > prior:
                self.worker_last_event[worker] = timestamp
        self.event_count += 1
        if action == "nlm_batch_source_add_attempt_completed":
            elapsed = _as_float(data.get("elapsed_s"))
            if elapsed is not None:
                self.add_elapsed.append(elapsed)
            video = data.get("video_id")
            if isinstance(video, str):
                self.add_attempts_by_video[video] = self.add_attempts_by_video.get(video, 0) + 1
            if "rpc_code=9" in str(data.get("error") or ""):
                self.rpc9_add_errors += 1
        elif action in {
            "nlm_batch_source_materialization_wait_succeeded",
            "nlm_batch_source_materialization_wait_failed",
            "nlm_batch_source_materialization_wait_timeout",
        }:
            elapsed = _as_float(data.get("elapsed_s"))
            if elapsed is not None:
                self.wait_elapsed.append(elapsed)
        elif action == "nlm_batch_source_materialization_wait_terminal_failure":
            self.materialization_terminal += 1
        elif action == "nlm_batch_source_content_fetch_completed":
            elapsed = _as_float(data.get("elapsed_s"))
            if elapsed is not None:
                self.fetch_elapsed.append(elapsed)
            queue_wait = _as_float(data.get("retry_queue_wait_time_s"))
            if queue_wait is not None:
                self.retry_queue_wait_s.append(queue_wait)
            video = data.get("video_id")
            attempts = data.get("attempts")
            if isinstance(video, str):
                count = attempts if isinstance(attempts, int) and attempts > 0 else 1
                self.fetch_attempts_by_video[video] = max(
                    self.fetch_attempts_by_video.get(video, 0), count
                )
                if count > 1:
                    self.fetch_retries += 1
        elif action == "nlm_batch_subbatch_add_completed":
            elapsed = _as_float(data.get("elapsed_s"))
            if elapsed is not None:
                self.subbatch_elapsed.append(elapsed)
        elif action == "nlm_batch_source_add_retry_skipped":
            if data.get("reason") == "rpc_code_9_failed_precondition":
                self.retry_skipped_rpc9 += 1
        elif action == "nlm_worker_notebook_cleanup_complete":
            self.notebook_cleanup_receipts.append(
                {
                    key: data.get(key)
                    for key in (
                        "status",
                        "outcome",
                        "deleted",
                        "failed",
                        "worker_notebook_count",
                        "active_nb_ids",
                        "notebook_prefix",
                        "run_id",
                    )
                    if key in data
                }
            )
        elif isinstance(action, str) and action.startswith("nlm_auth") and not action.endswith("_ok"):
            self.auth_failures += 1


def scan_account_events(chunk_root: Path, account: str) -> EventScan:
    scan = EventScan(account=account, source_dir=None)
    files = event_files_for_account(chunk_root, account)
    if not files:
        # Distinguish "no events dir" from "empty events dir" for evidence
        # freshness: a swept chunk has no directory at all.
        slug = account.replace(".", "-")
        if not (chunk_root / "accounts" / slug).is_dir() and not (
            chunk_root / "accounts" / account
        ).is_dir():
            scan.source_dir = None
        else:
            scan.source_dir = str((chunk_root / "accounts" / slug / "events"))
        return scan
    scan.source_dir = str(files[0].parent)
    for path in files:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    scan.parse_errors += 1
                    continue
                scan.consume(event)
    return scan


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def scan_chunk_events(chunk_root: Path, accounts: list[str]) -> dict[str, EventScan]:
    return {account: scan_account_events(chunk_root, account) for account in accounts}


# -- keepalive / control-plane / host evidence -------------------------------


def read_keepalive(log_path: Path, *, max_block_lines: int = 40) -> dict:
    """Parse the tail of the scheduled keepalive log (typed auth evidence).

    The log is line-based: ``[ISO] message``. A healthy run ends with
    ``keepalive complete`` after per-account ``passed`` lines. The monitor
    never treats the word "auth" in a failure string as auth evidence
    (AGENTS.md auth rule); only these typed probe receipts and the summary
    ``auth_preflight`` blocks count.
    """
    if not log_path.is_file():
        return {"available": False, "reason": "missing", "path": str(log_path)}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"available": False, "reason": f"unreadable:{type(exc).__name__}", "path": str(log_path)}
    block: list[str] = []
    for line in reversed(lines):
        block.append(line)
        if "keepalive start" in line:
            break
        if len(block) >= max_block_lines:
            break
    block.reverse()
    started = next((i for i, line in enumerate(block) if "keepalive start" in line), None)
    if started is not None:
        block = block[started:]
    passed = [line for line in block if "passed" in line]
    # Typed failure vocabulary from csf/nlm_keepalive.py: account probe
    # failures ("probe failed", "storage check failed", "is_connected is
    # False") are auth evidence; backup-push failures ("backup repo",
    # "refusing backup", ...) are exit-4-class warnings, NOT auth-blocked.
    auth_failed = [
        line
        for line in block
        if any(marker in line for marker in ("probe failed", "storage check failed", "is_connected is False"))
    ]
    backup_failed = [
        line
        for line in block
        if "backup" in line.lower() and line not in auth_failed and ("failed" in line.lower() or "missing" in line.lower() or "could not" in line.lower() or "refusing" in line.lower())
    ]
    complete = any("keepalive complete" in line for line in block)
    timestamps = [
        _parse_iso(line[1:25])
        for line in block
        if line.startswith("[") and len(line) > 25
    ]
    last_at = max((t for t in timestamps if t), default=None)
    healthy = bool(complete and passed and not auth_failed)
    return {
        "available": True,
        "path": str(log_path),
        "accounts_passed": len(passed),
        "auth_failure_lines": auth_failed[:3],
        "backup_warning_lines": backup_failed[:3],
        "completed": complete,
        "healthy": healthy,
        "last_entry_at": last_at.isoformat() if last_at else None,
        "last_entry_age_s": age_s(last_at),
    }


def probe_scheduled_tasks(task_names: list[str] | None = None) -> dict:
    """Read-only Windows Task Scheduler probe (control-plane evidence).

    Uses PowerShell Get-ScheduledTask because ``schtasks`` CLI listing is a
    known broken probe on this host (decision packet addendum). Returns per
    task: existence, state, action arguments, last run time/result. Never
    mutates anything.
    """
    import subprocess

    names = task_names or ["YtisUnattendedBacklog", "YtisNlmAuthKeepalive", "YtisStateBackup"]
    out: dict[str, dict] = {}
    for name in names:
        ps = (
            "$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
            "if (-not $t) { Write-Output '{\"exists\": false}'; exit 0 }; "
            "$i = Get-ScheduledTaskInfo -TaskName '%s' -ErrorAction SilentlyContinue; "
            "$a = $t.Actions | Select-Object -First 1; "
            "Write-Output (ConvertTo-Json -Compress -Depth 3 -InputObject @{ "
            "exists = $true; state = $t.State.ToString(); "
            "execute = $a.Execute; arguments = ($a.Arguments -join ' '); "
            "last_run_time = if ($i) { $i.LastRunTime.ToString('o') } else { $null }; "
            "last_result = if ($i) { $i.LastTaskResult } else { $null } })" % (name, name)
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            out[name] = {
                "available": False,
                "reason": "probe_failed:" + type(exc).__name__,
            }
            continue
        if proc.returncode != 0:
            out[name] = {"available": False, "reason": f"probe_exit_{proc.returncode}"}
            continue
        stdout = proc.stdout.strip()
        try:
            payload = json.loads(stdout.splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            out[name] = {"available": False, "reason": "probe_unparseable"}
            continue
        if not isinstance(payload, dict) or "exists" not in payload:
            out[name] = {"available": False, "reason": "probe_unparseable"}
            continue
        payload["available"] = True
        out[name] = payload
    return out


def resume_mechanism_effective(
    task_info: dict | None, canonical_state_path: Path
) -> tuple[bool | None, str]:
    """Decide whether a scheduled task can actually resume production.

    Effective means: the task exists, its arguments target the canonical
    production state path, and it passes ``--execute``. This reproduces the
    2026-08-17 finding: the daily task exited green while targeting the
    Aug-11 plan-only canary, so it could never resume production.
    """
    if not task_info or not task_info.get("available"):
        return None, "task_probe_unavailable"
    if not task_info.get("exists"):
        return False, "task_missing"
    arguments = str(task_info.get("arguments") or "")
    normalized = arguments.casefold().replace("/", "\\")
    target = str(canonical_state_path).casefold().replace("/", "\\")
    targets_canonical = target in normalized
    executes = "--execute" in arguments
    if targets_canonical and executes:
        return True, "targets_canonical_state_with_execute"
    reasons = []
    if not targets_canonical:
        reasons.append("does_not_target_canonical_state")
    if not executes:
        reasons.append("plan_only_no_execute")
    return False, ";".join(reasons)


def host_telemetry() -> dict:
    """Read-side host metrics only (decision packet §F verdicts).

    WORTH: disk free on the data drive; yt-is-owned browser process count
    and RSS (ownership = cmdline rooted under a yt-is browser profile);
    available memory (essentially free via psutil). Everything else was
    ruled NOT WORTH for v1.
    """
    try:
        import psutil
    except ImportError:
        return {"available": False, "reason": "psutil_missing"}
    data: dict = {"available": True}
    try:
        usage = psutil.disk_usage("P:\\")
        data["disk_free_gb"] = round(usage.free / (1024**3), 1)
        data["disk_total_gb"] = round(usage.total / (1024**3), 1)
    except (OSError, ValueError):
        data["disk_free_gb"] = None
    try:
        memory = psutil.virtual_memory()
        data["mem_available_gb"] = round(memory.available / (1024**3), 1)
    except (OSError, ValueError):
        data["mem_available_gb"] = None
    owned: list[dict] = []
    profile_markers = (
        ".data\\yt-is\\browser",
        "yt-is\\browser",
    )
    try:
        for proc in psutil.process_iter(["name", "cmdline", "memory_info"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                joined = " ".join(cmdline).casefold().replace("/", "\\")
                if not any(marker in joined for marker in profile_markers):
                    continue
                rss = proc.info.get("memory_info")
                owned.append(
                    {
                        "pid": proc.pid,
                        "name": proc.info.get("name"),
                        "rss_mb": round(rss.rss / (1024**2), 1) if rss else None,
                    }
                )
            except (psutil.Error, OSError):
                continue
    except psutil.Error:
        pass
    data["ytis_browser_process_count"] = len(owned)
    data["ytis_browser_rss_mb_total"] = round(sum(p["rss_mb"] or 0 for p in owned), 1)
    data["ytis_browser_processes"] = owned[:10]
    return data


def probe_notebook_inventory(accounts: list[str], *, timeout_s: int = 240) -> dict:
    """Opt-in NLM-side notebook inventory probe (audit mode, read-only).

    Runs the existing ``cleanup-worker-notebooks`` audit path once per
    account through the CLI with ``YTIS_NLM_ACCOUNT_PROFILE`` set and
    ``INTELLIGENCE_STREAM_LOG_DIR`` pointed at a monitor-owned temp dir, so
    the probe never writes into yt-is run artifacts. The audit receipt's
    ``worker_notebook_count`` / ``active_nb_ids`` fields supply observed vs
    expected; failures report UNKNOWN — never a fabricated zero.

    This spawns live NLM clients, which is why it is opt-in: the default
    health view reports run receipts only and marks the inventory probe
    ``not_run``.
    """
    import subprocess
    import tempfile

    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="ytis-monitor-nbprobe-") as tmp:
        events_dir = Path(tmp) / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["INTELLIGENCE_STREAM_LOG_DIR"] = str(events_dir)
        for account in accounts:
            env["YTIS_NLM_ACCOUNT_PROFILE"] = account
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(REPO_ROOT / "bin" / "csf-source"),
                        "cleanup-worker-notebooks",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    cwd=str(REPO_ROOT),
                    env=env,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                results[account] = {"status": "unknown", "reason": f"probe_failed:{type(exc).__name__}"}
                continue
            receipt = None
            for jsonl in sorted(events_dir.glob("*.jsonl")):
                for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or "nlm_worker_notebook_cleanup_complete" not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    receipt = {
                        key: data.get(key)
                        for key in (
                            "status",
                            "outcome",
                            "deleted",
                            "failed",
                            "worker_notebook_count",
                            "active_nb_ids",
                            "error",
                        )
                    }
            if receipt is None:
                results[account] = {
                    "status": "unknown",
                    "reason": "no_receipt",
                    "returncode": proc.returncode,
                    "stderr_tail": (proc.stderr or "")[-200:],
                }
            else:
                observed = receipt.get("worker_notebook_count")
                active = receipt.get("active_nb_ids")
                results[account] = {
                    "status": "probed",
                    "returncode": proc.returncode,
                    "observed_worker_notebooks": observed,
                    "expected_active_notebooks": active,
                    "stale_candidates": (
                        observed - active
                        if isinstance(observed, int) and isinstance(active, int)
                        else None
                    ),
                    "cleanup_status": receipt.get("status"),
                    "cleanup_error": receipt.get("error"),
                }
            # Each account writes its own trace file; clear between runs.
            for jsonl in events_dir.glob("*.jsonl"):
                jsonl.unlink()
    return results


def chunk_evidence_integrity(
    records: list[ChunkRecord], *, last_activity: datetime | None = None
) -> list[dict]:
    """State<->disk reference check with retention-aware classification.

    Classification (full prompt §9):
      EVIDENCE_AVAILABLE            referenced artifacts present
      EVIDENCE_EXPIRED_BY_POLICY    missing, but the run went quiet at least
                                    DEFAULT_MAX_AGE_DAYS ago, so the
                                    cleanup_staging sweep is the expected
                                    actor (drill-down degrades to DB-level)
      EVIDENCE_MISSING_UNEXPECTEDLY missing while the run is inside the
                                    retention horizon (actor unknown — the
                                    Aug-16 deleted-root incident class)
      EVIDENCE_INCOMPLETE           only part of the expected artifacts
                                    exist (e.g. summary present, events dir
                                    absent)
    """
    try:
        from csf.cleanup_staging import DEFAULT_MAX_AGE_DAYS

        horizon_s = float(DEFAULT_MAX_AGE_DAYS) * 86400.0
    except Exception:
        horizon_s = 7.0 * 86400.0
    quiet_age_s = age_s(last_activity)
    expired_horizon = quiet_age_s is not None and quiet_age_s > horizon_s
    problems: list[dict] = []
    for record in records:
        output_root = Path(record.output_root) if record.output_root else None
        summary_present = bool(record.summary_path and Path(record.summary_path).is_file())
        root_present = bool(output_root and output_root.is_dir())
        events_present = bool(
            output_root and any((output_root / "accounts").glob("*/events"))
        )
        if root_present and summary_present:
            classification = "EVIDENCE_AVAILABLE"
            if record.executed and not events_present:
                classification = "EVIDENCE_INCOMPLETE"
        elif not root_present and not summary_present:
            classification = (
                "EVIDENCE_EXPIRED_BY_POLICY"
                if expired_horizon
                else "EVIDENCE_MISSING_UNEXPECTEDLY"
            )
        else:
            classification = "EVIDENCE_INCOMPLETE"
        if classification != "EVIDENCE_AVAILABLE":
            problems.append(
                {
                    "chunk": record.index,
                    "classification": classification,
                    "output_root": record.output_root,
                    "output_root_present": root_present,
                    "summary_path": record.summary_path,
                    "summary_present": summary_present,
                    "events_present": events_present,
                    "retention_horizon_days": round(horizon_s / 86400.0, 1),
                    "run_quiet_age_s": round(quiet_age_s, 0) if quiet_age_s is not None else None,
                }
            )
    return problems


def now_epoch() -> float:
    return time.time()


def fresh_heartbeat(runtime: dict | None, *, threshold_s: float = HEARTBEAT_FRESH_S) -> tuple[bool | None, float | None]:
    """Classify supervisor heartbeat freshness from the runtime receipt."""
    if not runtime:
        return None, None
    heartbeat = runtime.get("heartbeat_at_epoch")
    if isinstance(heartbeat, bool) or not isinstance(heartbeat, (int, float)):
        return None, None
    age = max(0.0, now_epoch() - float(heartbeat))
    return age <= threshold_s, age
