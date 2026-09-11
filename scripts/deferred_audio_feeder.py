"""Deferred-audio backlog feeder: inventory, CPU transcription, ledgered eviction.

The deferred backlog is the kept audio under the visual media root whose
transcript never completed. This tool is the phase-2 lifecycle driver:

  inventory  rebuild the durable manifest (stores + filesystem walk)
  process    transcribe backlog items on CPU (never CUDA) with a checkpoint
  evict      dry-run manifest, then batch ledgered eviction of cached items
  measure    aggregate totals only

Deletion rule (phase-1, operator-pre-approved for this pool): audio is
unlinked only when a transcript row exists for the video (transcript
complete), or as a TTL-eligible partial-failure artifact; every unlink
appends a ledger row via the phase-1 helpers. Never a raw unlink.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_visual_worker import (  # noqa: E402
    DELETION_LEDGER_NAME,
    MIN_RECOVERABLE_AUDIO_BYTES,
    audio_deletable,
    delete_media_with_ledger,
)

MEDIA_ROOT = Path(os.environ.get("YTIS_VISUAL_MEDIA_ROOT", "P:/.data/yt-is/visual"))
BATCH_DB = Path(os.environ.get("YTIS_BATCH_DB", "P:/.data/yt-is/batch_status.sqlite"))
MANIFEST_PATH = REPO_ROOT / "docs" / "deferred-audio" / "manifest.json"
CHECKPOINT_PATH = Path(
    os.environ.get(
        "YTIS_FEEDER_CHECKPOINT",
        "P:/tmp/whisper-teardown/feeder-checkpoint.json",
    )
)
# Model-cache home for feeder workers. The machine default HF_HOME
# (P:\.model_cache -> junction -> P:\.cache\model_cache) gets swept by an
# automated cache-purge pass, so the feeder pins its own home under the
# pipeline's protected .data area; the model persists across runs.
MODEL_CACHE_HOME = Path(
    os.environ.get("YTIS_FEEDER_MODEL_CACHE", "P:/.data/yt-is/model_cache")
)
AUDIO_SUFFIXES = {".mka", ".mp3", ".m4a", ".wav", ".opus"}
CPU_MODEL = os.environ.get("YTIS_WHISPER_CPU_MODEL", "large-v3-turbo")
# Production worker: the CPU runner script. `python -m csf.whisper_worker`
# fail-fasts (exit 3221226505) during model load on this host ~80% of the
# time; the identical faster-whisper stack invoked as a plain script is
# stable, so the default is the script. Tests override with a module-mode
# stub via YTIS_FEEDER_WORKER.
WORKER_SCRIPT = os.environ.get(
    "YTIS_FEEDER_WORKER_SCRIPT", str(REPO_ROOT / "scripts" / "whisper_cpu_runner.py")
)
WORKER_MODULE = os.environ.get("YTIS_FEEDER_WORKER")
# Item budget: 30 min covers the largest observed CPU transcription
# (~11 min for a 28 MB item) with headroom for multi-hundred-MB files
# that clear the over-length guard. The visual worker's 900 s budget
# covers the same script on fresher, smaller queued audio; the two
# values differ by workload class, not by operation.
ITEM_TIMEOUT_S = float(os.environ.get("YTIS_FEEDER_TIMEOUT_S", "1800"))
ITEM_TIMEOUT_S = float(os.environ.get("YTIS_FEEDER_TIMEOUT_S", "1800"))
# Stop-if floor: P: free bytes below this halts processing (tests override
# to 0 since fixtures live on other drives).
MIN_FREE_BYTES = int(
    os.environ.get("YTIS_FEEDER_MIN_FREE_BYTES", str(2 * 1024 * 1024 * 1024))
)
# faster-whisper materializes the full feature array in RAM (~10.3x the
# audio bytes, observed: 3.5 GB audio -> 35.9 GiB float32 allocation).
# Items whose estimate exceeds this cap are reported unprocessable
# instead of crashing the worker.
MAX_EST_ALLOC_BYTES = int(
    os.environ.get("YTIS_FEEDER_MAX_ALLOC_BYTES", str(4 * 1024 * 1024 * 1024))
)
EST_ALLOC_RATIO = 10.5
# Consecutive-error cap: an item that fails this many times in a row is
# promoted to terminal `unprocessable` with its last error, instead of
# being retried at up to 30 min each, every run, forever. `cached`,
# `refused` and `unprocessable` are terminal; plain `error` retries.
MAX_CONSECUTIVE_ERRORS = int(os.environ.get("YTIS_FEEDER_MAX_ERRORS", "3"))
# Minimum intact large-v3-turbo model.bin size. The full snapshot is
# ~1.62 GB; interrupted downloads leave smaller blobs that make
# ctranslate2 fail-fast (exit 3221226505) with empty stderr. The
# pre-flight below reports this instead of burning a batch on crashes.
MIN_MODEL_BIN_BYTES = int(
    os.environ.get("YTIS_FEEDER_MIN_MODEL_BYTES", str(1_500_000_000))
)

STATUS_COMPLETE = "complete"


def classify_audio_row(status: str | None, size: int) -> str:
    """Pure: bucket one audio row.

    complete  — transcript/promotion status says complete (evictable)
    backlog   — audio present, transcript not complete, floor OK
    subfloor  — audio below the recoverable floor (never transcribed)
    """
    if size < MIN_RECOVERABLE_AUDIO_BYTES:
        return "subfloor"
    if audio_deletable(status):
        return STATUS_COMPLETE
    return "backlog"


def pick_distribution(items: list[dict], n: int) -> list[dict]:
    """Pure: deterministic size-spanning sample for inventory checks.

    Sorts by bytes ascending and takes n evenly spaced indices, so a
    small n still spans the smallest through the largest backlog item.
    Sampling only — never the work scheduler (it would re-inject the
    current max into every batch).
    """
    if n <= 0 or not items:
        return []
    ordered = sorted(items, key=lambda r: (r["bytes"], r["video_id"]))
    if n >= len(ordered):
        return list(ordered)
    last = len(ordered) - 1
    return [ordered[int(i * last / (n - 1))] for i in range(n)]


def pick_work(items: list[dict], n: int) -> list[dict]:
    """Pure: smallest-first work selection.

    Drains cheap items first so timeouts and RAM spikes concentrate in
    the tail, where the over-length guard and error cap handle them one
    at a time instead of inside every batch.
    """
    if n <= 0 or not items:
        return []
    ordered = sorted(items, key=lambda r: (r["bytes"], r["video_id"]))
    return ordered[:n] if n < len(ordered) else list(ordered)


def record_outcome(checkpoint: dict, video_id: str, result: dict) -> dict:
    """Pure: fold one item result into the checkpoint with the error cap.

    A non-terminal `error` following MAX_CONSECUTIVE_ERRORS-1 prior
    errors promotes the item to terminal `unprocessable`, carrying the
    last error as the reason.
    """
    prior = checkpoint.get(video_id, {})
    prior_errors = prior.get("consecutive_errors", 0) if isinstance(prior, dict) else 0
    state = result.get("state")
    if state == "error" and prior_errors + 1 >= MAX_CONSECUTIVE_ERRORS:
        entry = dict(result)
        entry["state"] = "unprocessable"
        entry["error"] = (
            f"error_cap: {prior_errors + 1} consecutive failures; "
            f"last: {result.get('error', '')[:200]}"
        )
        entry["consecutive_errors"] = prior_errors + 1
    else:
        entry = dict(result)
        entry["consecutive_errors"] = prior_errors + 1 if state == "error" else 0
    checkpoint[video_id] = entry
    return entry


def evictable(cached: bool) -> bool:
    """Pure: the deletion rule for feeder items.

    A cached transcript row is the recovery-path equivalent of a
    complete transcript row; reuse the phase-1 decision helper.
    """
    return audio_deletable(STATUS_COMPLETE if cached else None)


def check_model_cache(home: Path = MODEL_CACHE_HOME) -> dict:
    """Pre-flight: is the configured model snapshot intact enough to load?

    Resolves the snapshot for CPU_MODEL (not just the biggest blob — a
    large unrelated model must not satisfy the check), then requires the
    model.bin floor. A swept or partial snapshot makes ctranslate2
    fail-fast silently; callers must refuse the batch with this verdict
    instead.
    """
    hub = home / "hub"
    if not hub.is_dir():
        return {"ok": False, "error": f"model cache hub missing: {hub}"}
    slug = CPU_MODEL.replace("_", "-")
    candidates = [
        d for d in hub.glob("models--*")
        if d.is_dir() and slug in d.name
    ]
    if not candidates:
        return {
            "ok": False,
            "error": f"no cached snapshot for model {CPU_MODEL!r} under {hub}",
        }
    bins = sorted(
        (b for d in candidates for b in (d / "snapshots").glob("*/model.bin")),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not bins:
        return {
            "ok": False,
            "error": f"no model.bin in {CPU_MODEL!r} snapshot under {hub}",
        }
    biggest = bins[0]
    size = biggest.stat().st_size
    refs_main = biggest.parents[2] / "refs" / "main"
    if not refs_main.exists() and size < MIN_MODEL_BIN_BYTES:
        return {
            "ok": False,
            "error": (
                f"model.bin for {CPU_MODEL!r} partial "
                f"({size/1e9:.2f} GB < {MIN_MODEL_BIN_BYTES/1e9:.2f} GB floor, "
                "no refs/main commit marker): re-download first"
            ),
        }
    return {"ok": True, "model_bin": str(biggest), "bytes": size}


def reconcile_checkpoint(checkpoint: dict) -> dict:
    """Re-derive checkpoint terminal states from the transcript store.

    Returns (kept, dropped, report): entries whose cached claim no
    longer has a transcript row are dropped so the item retries;
    refused/unprocessable terminal states are kept as recorded.
    """
    from csf.cache import has_cached_transcript

    kept: dict = {}
    dropped: list[str] = []
    for video_id, result in checkpoint.items():
        state = result.get("state") if isinstance(result, dict) else None
        if state == "cached" and not has_cached_transcript(video_id):
            dropped.append(video_id)
            continue
        kept[video_id] = result
    return {
        "kept": kept,
        "dropped": dropped,
        "report": (
            f"checkpoint {len(checkpoint)} entries: kept {len(kept)}, "
            f"dropped {len(dropped)} stale-cached (will retry)"
        ),
    }


def _connect_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _status_maps() -> tuple[dict[str, str], dict[str, str]]:
    con = _connect_ro(BATCH_DB)
    try:
        ana = {
            str(v): str(s)
            for v, s in con.execute(
                "SELECT video_id, status FROM analysis_status"
            ).fetchall()
        }
        tstat = {
            str(v): str(s)
            for v, s in con.execute(
                "SELECT video_id, status FROM transcript_status"
            ).fetchall()
        }
    finally:
        con.close()
    return ana, tstat


def build_inventory() -> dict:
    """Walk the media root, join with stores, bucket every audio file."""
    ana, tstat = _status_maps()
    from csf.cache import get_shared_db_path

    cache_db = Path(get_shared_db_path())
    cached_ids: set[str] = set()
    if cache_db.exists():
        con = _connect_ro(cache_db)
        try:
            cached_ids = {
                str(r[0])
                for r in con.execute(
                    "SELECT DISTINCT video_id FROM transcript_cache"
                ).fetchall()
            }
        finally:
            con.close()

    items: list[dict] = []
    totals = {
        "backlog": {"files": 0, "bytes": 0},
        STATUS_COMPLETE: {"files": 0, "bytes": 0},
        "subfloor": {"files": 0, "bytes": 0},
    }
    for vdir in sorted(MEDIA_ROOT.iterdir()):
        if not vdir.is_dir():
            continue
        vid = vdir.name
        for f in sorted(vdir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            status = ana.get(vid) or tstat.get(vid) or "absent"
            bucket = classify_audio_row(status, size)
            totals[bucket]["files"] += 1
            totals[bucket]["bytes"] += size
            items.append(
                {
                    "video_id": vid,
                    "path": str(f),
                    "bytes": size,
                    "status": status,
                    "bucket": bucket,
                    "transcript_cached": vid in cached_ids,
                }
            )
    manifest = {
        "schema": 1,
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "media_root": str(MEDIA_ROOT),
        "floor_bytes": MIN_RECOVERABLE_AUDIO_BYTES,
        "totals": totals,
        "items": items,
    }
    return manifest


def write_manifest(manifest: dict, path: Path = MANIFEST_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    return path


def verify_sample(manifest: dict, n: int = 20) -> list[str]:
    """Re-derive statuses for n sampled items straight from the stores.

    Returns a list of mismatches (empty means the sample cross-checks).
    """
    backlog = [i for i in manifest["items"] if i["bucket"] == "backlog"]
    sample = pick_distribution(backlog, n)
    ana, tstat = _status_maps()
    mismatches: list[str] = []
    for item in sample:
        vid = item["video_id"]
        fresh = ana.get(vid) or tstat.get(vid) or "absent"
        if fresh != item["status"]:
            mismatches.append(f"{vid}: manifest={item['status']} store={fresh}")
        try:
            size = Path(item["path"]).stat().st_size
        except OSError:
            mismatches.append(f"{vid}: manifest file missing on disk")
            continue
        if size != item["bytes"]:
            mismatches.append(f"{vid}: bytes manifest={item['bytes']} disk={size}")
    return mismatches


def _disk_floor_bytes() -> int:
    return int(
        os.environ.get("YTIS_FEEDER_MIN_FREE_BYTES", str(MIN_FREE_BYTES))
    )


def _guard_volume() -> Path:
    """The filesystem the floor actually guards.

    MEDIA_ROOT is env-overridable onto other volumes; probing a fixed
    "P:/" would then monitor the wrong disk. Guard the anchor of the
    directory we write to.
    """
    root = MEDIA_ROOT if MEDIA_ROOT.is_absolute() else REPO_ROOT / MEDIA_ROOT
    return Path(root.anchor)


def _disk_free_ok() -> bool:
    return shutil.disk_usage(str(_guard_volume())).free >= _disk_floor_bytes()


def _floor_stop_message() -> str:
    return (
        f"STOP: disk free below {_disk_floor_bytes() / 1e9:.1f} GB floor "
        f"on {_guard_volume()}"
    )


def _checkpoint_load() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _checkpoint_save(state: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(CHECKPOINT_PATH)


def process_item(item: dict) -> dict:
    """Transcribe one backlog item on CPU and cache the transcript.

    Device policy: CUDA_VISIBLE_DEVICES is emptied for the worker
    subprocess so torch falls back to the CPU path (the CUDA teardown
    abort makes GPU unusable); CPU model pins to large-v3-turbo int8,
    matching the root-cause probes. The transcript goes through
    csf.cache.set_cached_transcript — the same cache write the visual
    worker recovery uses — so downstream consumers see normal rows.
    """
    import tempfile

    from csf.cache import set_cached_transcript
    from csf.paths import load_workspace_env

    load_workspace_env()
    # Pin the worker's HF home to the feeder-owned cache (the machine
    # default P:\.model_cache junction target gets swept by an automated
    # cache-purge pass, crashing model loads).
    env_overrides = {
        "CUDA_VISIBLE_DEVICES": "",
        "YTIS_WHISPER_CPU_MODEL": CPU_MODEL,
        "HF_HOME": str(MODEL_CACHE_HOME),
    }
    try:
        Path(MODEL_CACHE_HOME, "hub").mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass

    result_fd, result_name = tempfile.mkstemp(prefix="feeder_whisper_", suffix=".json")
    os.close(result_fd)
    result_path = Path(result_name)
    est_alloc = item["bytes"] * EST_ALLOC_RATIO
    if est_alloc > MAX_EST_ALLOC_BYTES:
        return {
            "state": "unprocessable",
            "error": (
                "over_length: est feature allocation "
                f"{est_alloc / 2**30:.1f} GiB exceeds "
                f"{MAX_EST_ALLOC_BYTES / 2**30:.0f} GiB cap"
            ),
        }
    env = dict(os.environ)
    env.update(env_overrides)
    if WORKER_MODULE:
        command = [
            sys.executable, "-m", WORKER_MODULE,
            "--audio-file", item["path"],
            "--lang", "en",
            "--result-path", str(result_path),
        ]
    else:
        command = [
            sys.executable, WORKER_SCRIPT,
            "--audio-file", item["path"],
            "--lang", "en",
            "--result-path", str(result_path),
        ]
    try:
        proc = subprocess.run(
            command, cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=ITEM_TIMEOUT_S, check=False, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stderr_tail = (proc.stderr or "").strip()[-200:]
        if not result_path.exists():
            return {
                "state": "error",
                "error": (
                    "whisper worker produced no result; "
                    f"stderr tail: {stderr_tail or '(empty)'}"
                ),
            }
        result_text = result_path.read_text(encoding="utf-8")
        if not result_text.strip():
            return {
                "state": "error",
                "error": (
                    "whisper worker crashed before writing a result "
                    f"(empty result file); stderr tail: "
                    f"{stderr_tail or '(empty)'}"
                ),
            }
        payload = json.loads(result_text)
    except subprocess.TimeoutExpired:
        return {"state": "error", "error": f"whisper timeout (>{ITEM_TIMEOUT_S:g}s)"}
    except Exception as exc:  # noqa: BLE001 - per-item isolation
        return {"state": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        result_path.unlink(missing_ok=True)

    if not payload.get("ok"):
        return {"state": "error", "error": str(payload.get("error"))[:300]}
    text = str(payload.get("transcript") or "")
    cached = set_cached_transcript(
        item["video_id"], "en", "whisper", text,
        metadata={
            "origin": "deferred_audio_feeder",
            "transcript_chars": len(text),
            "source_bytes": item["bytes"],
        },
    )
    if not cached:
        return {
            "state": "refused",
            "chars": len(text),
            "error": "transcript refused by cache boundary (sub-floor content)",
        }
    return {"state": "cached", "chars": len(text)}


def cmd_inventory(args: argparse.Namespace) -> int:
    manifest = build_inventory()
    path = write_manifest(manifest, Path(args.manifest) if args.manifest else MANIFEST_PATH)
    t = manifest["totals"]
    backlog_bytes = t["backlog"]["bytes"]
    print(f"manifest: {path}")
    print(f"backlog: {t['backlog']['files']} files, {backlog_bytes/1e9:.2f} GB")
    print(f"complete: {t[STATUS_COMPLETE]['files']} files, {t[STATUS_COMPLETE]['bytes']/1e9:.2f} GB")
    print(f"subfloor: {t['subfloor']['files']} files, {t['subfloor']['bytes']/1e6:.1f} MB")
    mismatches = verify_sample(manifest, args.verify_sample)
    if mismatches:
        for m in mismatches:
            print(f"SAMPLE MISMATCH: {m}")
        return 1
    print(f"sample cross-check: {args.verify_sample} rows OK")
    return 0


def _load_backlog(manifest_path: Path) -> list[dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [i for i in manifest["items"] if i["bucket"] == "backlog"]


def cmd_process(args: argparse.Namespace) -> int:
    if not _disk_free_ok():
        print(_floor_stop_message())
        return 3
    cache_verdict = check_model_cache()
    if not cache_verdict["ok"]:
        print(f"STOP: {cache_verdict['error']}")
        return 4
    manifest_path = Path(args.manifest) if args.manifest else MANIFEST_PATH
    if not manifest_path.exists():
        print(f"no manifest at {manifest_path}; run inventory first")
        return 2
    backlog = _load_backlog(manifest_path)
    checkpoint = _checkpoint_load()
    # Only cached/refused/unprocessable are terminal states; plain errors
    # retry on re-run so transient failures never strand an item.
    pending = [
        i for i in backlog
        if checkpoint.get(i["video_id"], {}).get("state")
        not in ("cached", "refused", "unprocessable")
        and not i["transcript_cached"]
    ]
    selected = pick_work(pending, args.limit)
    print(f"backlog {len(backlog)}, checkpointed {len(checkpoint)}, "
          f"processing {len(selected)} (CPU model {CPU_MODEL})")
    counts = {"cached": 0, "refused": 0, "errors": 0, "unprocessable": 0}
    for item in selected:
        result = process_item(item)
        result["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["bytes"] = item["bytes"]
        entry = record_outcome(checkpoint, item["video_id"], result)
        _checkpoint_save(checkpoint)
        counts[entry["state"] if entry["state"] in counts else "errors"] += 1
        print(f"{item['video_id']} [{item['bytes']/1e6:.1f} MB] -> {entry['state']} "
              f"chars={entry.get('chars', 0)} {str(entry.get('error', ''))[:120]}")
    print(f"done: cached={counts['cached']} refused={counts['refused']} "
          f"errors={counts['errors']} unprocessable={counts['unprocessable']}")
    return 0 if counts["errors"] == 0 else 1


def _cached_backlog_items(backlog: list[dict]) -> list[dict]:
    """Backlog rows whose transcript row now exists (deletion rule met)."""
    out = []
    for item in backlog:
        if Path(item["path"]).exists():
            out.append(item)
    return [i for i in out if evictable(True) and _cache_has(i["video_id"])]


def _cache_has(video_id: str) -> bool:
    from csf.cache import has_cached_transcript

    return has_cached_transcript(video_id)


def cmd_evict(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest) if args.manifest else MANIFEST_PATH
    if not manifest_path.exists():
        print(f"no manifest at {manifest_path}; run inventory first")
        return 2
    backlog = _load_backlog(manifest_path)
    evictable_items = _cached_backlog_items(backlog)
    total_bytes = sum(i["bytes"] for i in evictable_items)
    print(f"evictable (transcript row complete): {len(evictable_items)} files, "
          f"{total_bytes/1e9:.2f} GB")
    if args.dry_run:
        manifest = {
            "dry_run": True,
            "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "unlinks": 0,
            "files": len(evictable_items),
            "bytes": total_bytes,
            "items": [
                {"video_id": i["video_id"], "path": i["path"], "bytes": i["bytes"]}
                for i in evictable_items
            ],
        }
        out = Path(args.out) if args.out else CHECKPOINT_PATH.parent / "dryrun.json"
        out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"dry-run manifest: {out} (zero unlinks performed)")
        return 0
    if not evictable_items:
        print("nothing evictable")
        return 0
    receipts = []
    for item in evictable_items:
        receipts.append(
            delete_media_with_ledger(
                Path(item["path"]), video_id=item["video_id"],
                reason="transcript_complete", media_root=MEDIA_ROOT,
            )
        )
    deleted = sum(1 for r in receipts if r.get("deleted"))
    failed = [r for r in receipts if not r.get("deleted")]
    print(f"unlinked {deleted}, ledger {DELETION_LEDGER_NAME}, "
          f"failures {len(failed)}")
    for r in failed[:5]:
        print(f"FAILED: {r}")
    return 0 if not failed else 1


def cmd_measure(args: argparse.Namespace) -> int:
    manifest = build_inventory()
    t = manifest["totals"]
    print(f"backlog: {t['backlog']['files']} files, {t['backlog']['bytes']/1e9:.2f} GB")
    print(f"complete: {t[STATUS_COMPLETE]['files']} files")
    print(f"subfloor: {t['subfloor']['files']} files")
    return 0


def run_drain_phase(limit: int, manifest_path: Path | None = None,
                    checkpoint_path: Path | None = None,
                    max_runtime_s: float | None = None) -> dict:
    """One bounded drain pass for embedding in the normal worker run.

    Reconcile, CPU-transcribe up to `limit` backlog items, then dry-run
    and apply ledgered eviction of what completed. `max_runtime_s` caps
    the pass by wall clock (checked before each item) so the embedded
    drain cannot overrun the caller's runtime budget. Returns a summary
    dict; never raises on per-item failures (they land in the counts).
    """
    import time as _time

    global CHECKPOINT_PATH
    manifest_file = manifest_path or MANIFEST_PATH
    if checkpoint_path is not None:
        CHECKPOINT_PATH = checkpoint_path
    summary: dict = {
        "limit": limit, "processed": {}, "evicted": 0,
        "evicted_bytes": 0, "stopped": None,
    }
    if not _disk_free_ok():
        summary["stopped"] = "disk_floor"
        return summary
    cache_verdict = check_model_cache()
    if not cache_verdict["ok"]:
        summary["stopped"] = f"model_cache: {cache_verdict['error']}"
        return summary

    checkpoint = _checkpoint_load()
    outcome = reconcile_checkpoint(checkpoint)
    _checkpoint_save(outcome["kept"])
    summary["reconciled"] = outcome["report"]
    checkpoint = outcome["kept"]

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    pending = [
        i for i in manifest["items"] if i["bucket"] == "backlog"
        and checkpoint.get(i["video_id"], {}).get("state")
        not in ("cached", "refused", "unprocessable")
        and not i["transcript_cached"]
    ]
    counts = {"cached": 0, "refused": 0, "errors": 0, "unprocessable": 0}
    started = _time.monotonic()
    for item in pick_work(pending, limit):
        if (max_runtime_s is not None
                and _time.monotonic() - started > max_runtime_s):
            summary["stopped"] = "drain_runtime"
            break
        result = process_item(item)
        result["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["bytes"] = item["bytes"]
        entry = record_outcome(checkpoint, item["video_id"], result)
        _checkpoint_save(checkpoint)
        state = entry["state"]
        counts[state if state in counts else "errors"] += 1
    summary["processed"] = counts

    evict_ns = argparse.Namespace(
        dry_run=False, apply=True, manifest=str(manifest_file),
        out=str(CHECKPOINT_PATH.parent / "dryrun.json"),
    )
    # Dry-run first for the manifest record, then apply.
    evict_ns.dry_run, evict_ns.apply = True, False
    cmd_evict(evict_ns)
    evict_ns.dry_run, evict_ns.apply = False, True
    before = len(checkpoint)
    cmd_evict(evict_ns)
    summary["evicted_checkpointed"] = before
    return summary


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Drop stale cached claims from the checkpoint so items retry."""
    checkpoint = _checkpoint_load()
    outcome = reconcile_checkpoint(checkpoint)
    _checkpoint_save(outcome["kept"])
    print(outcome["report"])
    for video_id in outcome["dropped"][:10]:
        print(f"dropped stale claim: {video_id}")
    return 0


def cmd_drain(args: argparse.Namespace) -> int:
    summary = run_drain_phase(
        args.limit,
        manifest_path=Path(args.manifest) if args.manifest else None,
        checkpoint_path=Path(args.checkpoint) if args.checkpoint else None,
    )
    print(json.dumps(summary, indent=1))
    return 0 if summary.get("stopped") is None else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser("inventory", help="rebuild the durable manifest")
    p_inv.add_argument("--manifest", default=None)
    p_inv.add_argument("--verify-sample", type=int, default=20)
    p_inv.set_defaults(func=cmd_inventory)

    p_proc = sub.add_parser("process", help="CPU-transcribe backlog items")
    p_proc.add_argument("--limit", type=int, default=5)
    p_proc.add_argument("--manifest", default=None)
    p_proc.set_defaults(func=cmd_process)

    p_evict = sub.add_parser("evict", help="dry-run or apply ledgered eviction")
    p_evict.add_argument("--dry-run", action="store_true")
    p_evict.add_argument("--apply", action="store_true")
    p_evict.add_argument("--manifest", default=None)
    p_evict.add_argument("--out", default=None)
    p_evict.set_defaults(func=cmd_evict)

    p_meas = sub.add_parser("measure", help="aggregate totals only")
    p_meas.set_defaults(func=cmd_measure)

    p_rec = sub.add_parser(
        "reconcile", help="drop stale cached claims from the checkpoint"
    )
    p_rec.set_defaults(func=cmd_reconcile)

    p_drain = sub.add_parser(
        "drain", help="one bounded reconcile/process/evict pass"
    )
    p_drain.add_argument("--limit", type=int, default=25)
    p_drain.add_argument("--manifest", default=None)
    p_drain.add_argument("--checkpoint", default=None)
    p_drain.set_defaults(func=cmd_drain)

    args = parser.parse_args(argv)
    if args.command == "evict" and args.dry_run == args.apply:
        parser.error("evict needs exactly one of --dry-run / --apply")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
