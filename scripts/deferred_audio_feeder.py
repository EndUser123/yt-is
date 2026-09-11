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
AUDIO_SUFFIXES = {".mka", ".mp3", ".m4a", ".wav", ".opus"}
CPU_MODEL = os.environ.get("YTIS_WHISPER_CPU_MODEL", "large-v3-turbo")
# Worker module override for tests (process boundary double); production
# default is the real csf.whisper_worker.
WORKER_MODULE = os.environ.get("YTIS_FEEDER_WORKER", "csf.whisper_worker")
ITEM_TIMEOUT_S = float(os.environ.get("YTIS_FEEDER_TIMEOUT_S", "1800"))
# Stop-if floor: P: free bytes below this halts processing (tests override
# to 0 since fixtures live on other drives).
MIN_FREE_BYTES = int(
    os.environ.get("YTIS_FEEDER_MIN_FREE_BYTES", str(2 * 1024 * 1024 * 1024))
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
    """Pure: deterministic size-spanning sample.

    Sorts by bytes ascending and takes n evenly spaced indices, so a
    small n still spans the smallest through the largest backlog item.
    """
    if n <= 0 or not items:
        return []
    ordered = sorted(items, key=lambda r: (r["bytes"], r["video_id"]))
    if n >= len(ordered):
        return list(ordered)
    last = len(ordered) - 1
    return [ordered[int(i * last / (n - 1))] for i in range(n)]


def evictable(cached: bool) -> bool:
    """Pure: the deletion rule for feeder items.

    A cached transcript row is the recovery-path equivalent of a
    complete transcript row; reuse the phase-1 decision helper.
    """
    return audio_deletable(STATUS_COMPLETE if cached else None)


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


def _disk_free_ok() -> bool:
    floor = int(
        os.environ.get("YTIS_FEEDER_MIN_FREE_BYTES", str(MIN_FREE_BYTES))
    )
    return shutil.disk_usage("P:/").free >= floor


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

    result_fd, result_name = tempfile.mkstemp(prefix="feeder_whisper_", suffix=".json")
    os.close(result_fd)
    result_path = Path(result_name)
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["YTIS_WHISPER_CPU_MODEL"] = CPU_MODEL
    command = [
        sys.executable, "-m", WORKER_MODULE,
        "--audio-file", item["path"],
        "--lang", "en",
        "--result-path", str(result_path),
    ]
    try:
        subprocess.run(
            command, cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=ITEM_TIMEOUT_S, check=False, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not result_path.exists():
            return {"state": "error", "error": "whisper worker produced no result"}
        payload = json.loads(result_path.read_text(encoding="utf-8"))
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
    delta = backlog_bytes - 61.85e9
    print(f"delta vs 61.85 GB (2026-09-10): {delta/1e9:+.2f} GB")
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
        print("STOP: disk free below 2 GB floor")
        return 3
    manifest_path = Path(args.manifest) if args.manifest else MANIFEST_PATH
    if not manifest_path.exists():
        print(f"no manifest at {manifest_path}; run inventory first")
        return 2
    backlog = _load_backlog(manifest_path)
    checkpoint = _checkpoint_load()
    pending = [
        i for i in backlog
        if i["video_id"] not in checkpoint and not i["transcript_cached"]
    ]
    selected = pick_distribution(pending, args.limit)
    print(f"backlog {len(backlog)}, checkpointed {len(checkpoint)}, "
          f"processing {len(selected)} (CPU model {CPU_MODEL})")
    ok = refused = errors = 0
    for item in selected:
        result = process_item(item)
        result["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        result["bytes"] = item["bytes"]
        checkpoint[item["video_id"]] = result
        _checkpoint_save(checkpoint)
        if result["state"] == "cached":
            ok += 1
        elif result["state"] == "refused":
            refused += 1
        else:
            errors += 1
        print(f"{item['video_id']} [{item['bytes']/1e6:.1f} MB] -> {result['state']} "
              f"chars={result.get('chars', 0)} {result.get('error', '')[:120]}")
    print(f"done: cached={ok} refused={refused} errors={errors}")
    return 0 if errors == 0 else 1


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
        out = Path(args.out) if args.out else Path("dryrun.json")
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

    args = parser.parse_args(argv)
    if args.command == "evict" and args.dry_run == args.apply:
        parser.error("evict needs exactly one of --dry-run / --apply")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
