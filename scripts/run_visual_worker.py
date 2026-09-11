#!/usr/bin/env python3
"""Visual pipeline worker: claim -> paced download -> two-pass frames -> OCR -> artifacts.

Processes ``visual_jobs`` one at a time under the rate-limit-first contract
(``csf.visual.media_fetch``): serialized downloads, conservative yt-dlp pacing,
an hourly download budget, and a durable 429 cooldown that stops the run.

Per job: download (1080p cap) -> pass-1 frames (scene + periodic floor, 640px,
timestamps via showinfo) -> per-frame EasyOCR -> code-dense timestamps ->
pass-2 native-res re-capture -> audio remux kept for the Whisper stack ->
``visual_artifacts`` row -> idempotent ingestion receipt -> source video
evicted. Frames and audio persist under ``<media_root>/<video_id>/``.

Never touches ``analysis_status.status``; coexists with the live NLM drain.

Usage:
  python scripts/run_visual_worker.py --dry-run
  python scripts/run_visual_worker.py --max-jobs 3 --run-id smoke01
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import (  # noqa: E402
    get_batch_db_path,
    run_v3_visual_queue_migration,
    set_negative_cache,
)
from csf.paths import load_workspace_env  # noqa: E402
from csf.visual import frame_sampler, jobs as visual_jobs, media_fetch  # noqa: E402
from csf.visual.frame_sampler import _ffmpeg_binary  # noqa: E402

NEGATIVE_CACHE_TERMINAL_TTL_S = 3650 * 24 * 3600


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


# Retention: audio may be unlinked only for complete-transcript rows and
# source artifacts only past the partial TTL. Every audio or partial-source
# unlink appends a ledger row (video-source eviction on the visual path
# stays unledgered). Backlog removals run only from operator-approved
# dry-run manifests, never here.
PARTIAL_SOURCE_TTL_S = float(os.environ.get("YTIS_VISUAL_PARTIAL_TTL_S", "3600"))
DELETION_LEDGER_NAME = "deletion-ledger.jsonl"
# Recovery floor: audio smaller than this never reaches Whisper (0-byte and
# trunc files produce garbage transcripts while spending full slots).
MIN_RECOVERABLE_AUDIO_BYTES = 16384


def audio_deletable(transcript_status: str | None) -> bool:
    """Pure decision: audio may go only once the transcript is complete."""
    return transcript_status == "complete"


def maybe_evict_audio(
    *, video_id: str, audio_path: Path, db_path: Path, media_root: Path
) -> dict | None:
    """Second pass of the audio lifecycle.

    Recovery only caches the transcript; promotion to complete runs through
    the separate reviewed gate. This pass evicts the audio once the row
    reads complete, and does nothing before that. Returns the deletion
    receipt, or None when the row is not complete yet.
    """
    if not audio_deletable(_analysis_status_for(video_id, db_path)):
        return None
    return delete_media_with_ledger(
        audio_path, video_id=video_id,
        reason="transcript_complete",
        media_root=media_root,
    )


def delete_media_with_ledger(
    path: Path, *, video_id: str, reason: str, media_root: Path
) -> dict:
    """Unlink one media file and append its ledger row.

    Filesystem (OSError) failures are returned, never raised.
    """
    size = 0
    try:
        size = path.stat().st_size
        path.unlink()
        deleted = True
    except FileNotFoundError:
        deleted = False
    except OSError as exc:
        return {"deleted": False, "bytes": size, "error": str(exc)[:200]}
    if deleted:
        row = {
            "ts": _utcnow_iso(),
            "action": "delete",
            "path": str(path),
            "bytes": size,
            "video_id": video_id,
            "reason": reason,
        }
        try:
            with open(media_root / DELETION_LEDGER_NAME, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError as exc:
            return {"deleted": True, "bytes": size, "ledger_error": str(exc)[:200]}
    return {"deleted": deleted, "bytes": size}


def sweep_stale_partials(video_dir: Path, *, video_id: str, media_root: Path,
                         ttl_s: float = PARTIAL_SOURCE_TTL_S,
                         db_path: str | Path | None = None) -> list[dict]:
    """Remove partial-failure source artifacts older than the TTL, ledgered.

    TTL alone is a fallback signal: file mtime cannot prove the owning
    job failed, so a video holding a live job claim is always skipped
    (lease check) regardless of artifact age.
    """
    now = time.time()
    receipts = []
    try:
        candidates = sorted(video_dir.glob("source.*"))
    except OSError:
        return receipts
    leased = False
    if db_path is not None:
        try:
            leased = visual_jobs.video_has_active_job(video_id, db_path)
        except Exception:  # noqa: BLE001 - lease check must never block sweep
            leased = False
    if leased:
        return [{"video_id": video_id, "skipped": "active_job_lease"}]
    for path in candidates:
        try:
            age_s = now - path.stat().st_mtime
        except OSError:
            continue
        if age_s < ttl_s:
            continue
        receipts.append(
            delete_media_with_ledger(
                path, video_id=video_id,
                reason=f"stale_partial_ttl age_s={int(age_s)}",
                media_root=media_root,
            )
        )
    return receipts


def _dir_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def remux_audio(video_path: Path, dest_dir: Path) -> Path | None:
    """Copy the audio track for the Whisper stack (no re-encode).

    Matroska (.mka) is the container: it accepts both opus (webm merges) and
    aac (mp4 merges) under ``-c:a copy``, and faster-whisper reads it via
    ffmpeg. An .m4a target silently fails on opus sources.
    """
    dest = dest_dir / "audio.mka"
    cmd = [
        _ffmpeg_binary(), "-hide_banner", "-nostdin", "-y",
        "-i", str(video_path),
        "-vn", "-c:a", "copy",
        str(dest),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: bare ffmpeg under pythonw flashes a console window
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.unlink(missing_ok=True)
    return None


def artifact_content_hash(frames: list[Path], native: list[Path], ocr_text: str) -> str:
    h = hashlib.sha256()
    for frame in [*frames, *native]:
        h.update(frame.name.encode("utf-8"))
        try:
            h.update(str(frame.stat().st_size).encode("ascii"))
        except OSError:
            h.update(b"missing")
    h.update(ocr_text.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _analysis_status_for(video_id: str, db_path: Path) -> str | None:
    import sqlite3

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    try:
        row = conn.execute(
            "SELECT status FROM analysis_status WHERE video_id = ?", (video_id,)
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None


def maybe_recover_transcript(
    video_id: str, audio_path: Path, *, db_path: Path, run_id: str
) -> dict:
    """Local transcript recovery (operator policy 2026-08-18).

    If the video's transcript never completed (failed or absent), transcribe
    the kept audio here — the one media download covers frames AND the
    transcript. The result is written to the shared transcript cache with
    full provenance; canonical analysis_status promotion stays a separate
    reviewed gate (the exact promoter), so a short transcript can never
    silently flip a failed row to complete.
    """
    status = _analysis_status_for(video_id, db_path)
    if status == "complete":
        return {"attempted": False, "reason": "transcript_already_complete"}
    if status is None:
        return {"attempted": False, "reason": "no_analysis_row"}

    try:
        if audio_path.stat().st_size < MIN_RECOVERABLE_AUDIO_BYTES:
            return {"attempted": False, "reason": "audio_too_small"}
    except OSError:
        return {"attempted": False, "reason": "audio_unreadable"}

    import tempfile

    timeout_s = float(os.environ.get("YTIS_VISUAL_TRANSCRIBE_TIMEOUT_S", "900"))
    # 15 min item budget for queued recovery audio (fresher and smaller
    # on average than backlog items, which get 1800 s in the feeder).
    # Both wrap the same runner script; the values differ by workload
    # class, and the relay/drain wrappers derive their deadlines from
    # these per-item budgets rather than guessing independently.
    # Same over-length fence as the feeder (lazy import: the feeder
    # imports this module's deletion helpers, so a top-level import
    # would cycle). A concurrent visual job on a multi-GB file must not
    # bypass the only OOM fence.
    from scripts.deferred_audio_feeder import (
        EST_ALLOC_RATIO as _EST_ALLOC_RATIO,
    )
    from scripts.deferred_audio_feeder import (
        MAX_EST_ALLOC_BYTES as _MAX_EST_ALLOC_BYTES,
    )
    try:
        _audio_bytes = audio_path.stat().st_size
    except OSError:
        return {"attempted": False, "reason": "audio_unreadable"}
    if _audio_bytes * _EST_ALLOC_RATIO > _MAX_EST_ALLOC_BYTES:
        return {
            "attempted": True, "ok": False,
            "error": (
                "over_length: est feature allocation "
                f"{_audio_bytes * _EST_ALLOC_RATIO / 2**30:.1f} GiB exceeds "
                f"{_MAX_EST_ALLOC_BYTES / 2**30:.0f} GiB cap"
            ),
        }
    result_fd, result_name = tempfile.mkstemp(prefix="visual_whisper_", suffix=".json")
    os.close(result_fd)
    result_path = Path(result_name)
    command = [
        sys.executable, str(REPO_ROOT / "scripts" / "whisper_cpu_runner.py"),
        "--audio-file", str(audio_path),
        "--lang", "en",
        "--result-path", str(result_path),
    ]
    # Stable worker invocation: `python -m csf.whisper_worker` fail-fasts
    # (exit 3221226505) during model load on this host; the identical
    # faster-whisper stack as a plain script is stable. Same env the
    # deferred-audio feeder pins: CPU-only, feeder-owned model cache
    # (the machine HF_HOME junction target gets swept by a purge pass).
    worker_env = dict(os.environ)
    worker_env["CUDA_VISIBLE_DEVICES"] = ""
    worker_env.setdefault(
        "HF_HOME", str(Path(os.environ.get(
            "YTIS_FEEDER_MODEL_CACHE", "P:/.data/yt-is/model_cache",
        ))),
    )
    try:
        subprocess.run(
            command, cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=timeout_s, check=False, env=worker_env,
        )
        if not result_path.exists():
            return {"attempted": True, "ok": False, "error": "whisper worker produced no result"}
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        return {"attempted": True, "ok": False, "error": f"whisper timeout (>{timeout_s:g}s)"}
    except Exception as exc:
        return {"attempted": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        result_path.unlink(missing_ok=True)

    if not payload.get("ok"):
        return {"attempted": True, "ok": False, "error": str(payload.get("error"))[:300]}
    text = str(payload.get("transcript") or "")
    chars = len(text)
    words = len(text.split())
    band = "lt21" if chars < 21 else "21-499" if chars < 500 else "gte500"
    from csf.cache import set_cached_transcript

    cached = set_cached_transcript(
        video_id,
        "en",
        "whisper",
        text,
        metadata={
            "origin": "visual_worker",
            "run_id": run_id,
            "transcript_chars": chars,
            "transcript_words": words,
            "transcript_length_band": band,
            "prior_analysis_status": status,
        },
    )
    if not cached:
        return {
            "attempted": True,
            "ok": False,
            "chars": chars,
            "words": words,
            "length_band": band,
            "cached": False,
            "error": "transcript refused by cache boundary (sub-floor content)",
        }
    return {
        "attempted": True,
        "ok": True,
        "chars": chars,
        "words": words,
        "length_band": band,
        "cached": True,
        "promotion_candidate": chars >= 500,
    }


def store_visual_artifact(
    video_id: str,
    *,
    content_hash: str,
    frames_dir: Path,
    ocr_text: str,
    visual_tags: list[str],
    db_path: Path,
) -> None:
    import sqlite3

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute(
            """INSERT INTO visual_artifacts
               (video_id, version, content_hash, frames_dir, ocr_text, visual_tags,
                perceptual_hash_list, created_at)
               VALUES (?, 1, ?, ?, ?, ?, NULL, ?)
               ON CONFLICT(video_id, version) DO UPDATE SET
                   content_hash = excluded.content_hash,
                   frames_dir = excluded.frames_dir,
                   ocr_text = excluded.ocr_text,
                   visual_tags = excluded.visual_tags,
                   created_at = excluded.created_at""",
            (
                video_id,
                content_hash,
                str(frames_dir),
                ocr_text[:200000],
                json.dumps(visual_tags),
                _utcnow_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def process_one(
    job: dict,
    *,
    db_path: Path,
    run_root: Path,
    run_id: str,
    skip_ocr: bool,
) -> dict:
    """Run one claimed visual job end to end. Returns the job receipt."""
    from csf.ingestion import publish_artifact
    from csf.profiles import promote_profile

    video_id = job["video_id"]
    started = time.monotonic()
    receipt: dict = {
        "video_id": video_id,
        "job_id": job["job_id"],
        "attempt": job["attempt_count"],
        "started_at": _utcnow_iso(),
    }
    video_dir = media_fetch.media_root(db_path) / video_id

    # Job mode (operator policy, 2026-08-19): the need decides the download.
    # Transcript failed/missing -> the need is AUDIO (Whisper recovery):
    # audio-only stream, no frame passes, no video kept. Transcript complete
    # -> the need is VISUAL: full video for frames (audio not needed).
    transcript_status = _analysis_status_for(video_id, db_path)
    audio_recovery_mode = transcript_status is not None and transcript_status != "complete"
    receipt["mode"] = "audio_recovery" if audio_recovery_mode else "visual"

    receipt["partial_sweep"] = sweep_stale_partials(
        video_dir, video_id=video_id,
        media_root=media_fetch.media_root(db_path),
        db_path=db_path,
    )
    download = media_fetch.download_video(
        video_id, db_path=db_path, dest_dir=video_dir, audio_only=audio_recovery_mode
    )
    receipt["download"] = {k: download.get(k) for k in ("ok", "error_class", "bytes", "elapsed_s", "retry_after_s")}
    if not download.get("ok"):
        error_class = download.get("error_class", "download_failed")
        if error_class == "unavailable":
            set_negative_cache(
                video_id,
                f"visual:unavailable:{download.get('error_tail', '')[:200]}",
                source="visual",
                last_stage="download",
                ttl_seconds=NEGATIVE_CACHE_TERMINAL_TTL_S,
            )
        outcome = visual_jobs.fail_visual_job(
            video_id,
            error_class=error_class,
            failure_reason=str(download.get("error_tail", ""))[:500],
            retry_after_s=float(download.get("retry_after_s", 30.0) or 30.0),
            penalize_attempt=error_class
            not in {"rate_limited", "budget_exhausted", "cookie_source"},
            db_path=db_path,
        )
        visual_jobs.log_visual_attempt(
            video_id, profile=job.get("profile"), provider="yt-dlp",
            outcome=error_class, latency_ms=(time.monotonic() - started) * 1000,
            error_class=error_class, db_path=db_path,
        )
        receipt["result"] = {**download, "job_outcome": outcome}
        receipt["finished_at"] = _utcnow_iso()
        return receipt

    media_path = Path(download["path"])

    if audio_recovery_mode:
        # Audio-only job: rename the raw stream to the canonical audio.mka
        # container (remux, no re-encode) and run Whisper recovery. No frames,
        # no video retention.
        receipt["audio_policy"] = "needed"
        audio_path = remux_audio(media_path, video_dir)
        media_path.unlink(missing_ok=True)
        for leftover in video_dir.glob("source.*"):
            leftover.unlink(missing_ok=True)
        if audio_path is None:
            visual_jobs.fail_visual_job(
                video_id, error_class="audio_remux_failed",
                failure_reason="audio-only download could not be remuxed",
                retry_after_s=60.0, db_path=db_path,
            )
            receipt["result"] = {"ok": False, "error_class": "audio_remux_failed"}
            receipt["finished_at"] = _utcnow_iso()
            return receipt
        receipt["transcript_recovery"] = maybe_recover_transcript(
            video_id, audio_path, db_path=db_path, run_id=run_id
        )
        visual_jobs.complete_visual_job(
            video_id, status="complete", last_stage="audio_recovery",
            db_path=db_path,
        )
        evicted = maybe_evict_audio(
            video_id=video_id, audio_path=audio_path, db_path=db_path,
            media_root=media_fetch.media_root(db_path),
        )
        if evicted is not None:
            receipt["audio_evicted"] = evicted
        visual_jobs.log_visual_attempt(
            video_id, profile=job.get("profile"), provider="yt-dlp+whisper",
            outcome="ok", latency_ms=(time.monotonic() - started) * 1000,
            db_path=db_path,
        )
        receipt["result"] = {
            "ok": True,
            "status": "complete",
            "mode": "audio_recovery",
            "audio_bytes": audio_path.stat().st_size,
            "artifact_bytes": _dir_bytes(video_dir),
        }
        receipt["finished_at"] = _utcnow_iso()
        receipt["elapsed_s"] = round(time.monotonic() - started, 3)
        return receipt

    video_path = media_path
    try:
        sample = frame_sampler.extract_pass1(video_path, video_dir)
        # Persist the probed duration back to the catalog (operator quick win
        # #3: duration was 1.4% populated; the worker probes every video).
        if sample.duration_s and sample.duration_s > 0:
            try:
                import sqlite3 as _sq
                _conn = _sq.connect(str(db_path), timeout=10.0)
                _conn.execute("PRAGMA busy_timeout=5000")
                _conn.execute(
                    "UPDATE video_catalog SET duration = ? WHERE video_id = ? AND (duration IS NULL OR duration = 0)",
                    (int(sample.duration_s), video_id),
                )
                _conn.commit()
                _conn.close()
            except Exception:
                pass  # duration persistence is opportunistic, never blocking
        receipt["pass1"] = {
            "frames": len(sample.frames),
            "timestamps_exact": sample.timestamps_exact,
            "capped": sample.capped,
            "duration_s": sample.duration_s,
        }

        per_frame_text: list[str] = []
        ocr_skipped_reason = None
        if skip_ocr:
            ocr_skipped_reason = "skip_ocr_flag"
        elif not sample.frames:
            ocr_skipped_reason = "no_frames"
        else:
            try:
                from csf.ocr_client import extract_text_per_frame

                per_frame_text = extract_text_per_frame(sample.frames)
            except Exception as exc:  # model download failure, etc.
                ocr_skipped_reason = f"ocr_unavailable:{type(exc).__name__}"
        if ocr_skipped_reason:
            receipt["ocr"] = {"skipped": True, "reason": ocr_skipped_reason}

        joined_ocr = "\n".join(per_frame_text)
        profile_result = promote_profile(joined_ocr) if joined_ocr else None
        native_frames: list[Path] = []
        if per_frame_text:
            timestamps = frame_sampler.select_code_dense_timestamps(sample, per_frame_text)
            receipt["ocr"] = {
                "skipped": False,
                "chars": len(joined_ocr),
                "code_dense_timestamps": len(timestamps),
            }
            if timestamps:
                native_frames = frame_sampler.reextract_native(
                    video_path, timestamps, video_dir
                )
                receipt["pass2_native_frames"] = len(native_frames)

        clip_tags: list[str] = []
        if sample.frames:
            try:
                from csf.clip_client import tag_frames

                clip_tags = tag_frames(sample.frames)
            except Exception:
                clip_tags = []

        # Visual mode: transcript is complete by definition (mode selection
        # above), so there is no audio need — frames only, video evicted.
        receipt["audio_policy"] = "not_needed_transcript_complete"
        receipt["audio_kept"] = False

        content_hash = artifact_content_hash(sample.frames, native_frames, joined_ocr)
        store_visual_artifact(
            video_id,
            content_hash=content_hash,
            frames_dir=video_dir,
            ocr_text=joined_ocr,
            visual_tags=clip_tags,
            db_path=db_path,
        )
        publish = publish_artifact(
            video_id, "visual_frames", content_hash, db_path=db_path
        )

        # Over-image policy: keep frames; evict only the video file. Audio is
        # never written on this path (audio_kept False); audio lifecycle for
        # recovery jobs lives in the audio_recovery block above.
        video_path.unlink(missing_ok=True)
        for leftover in video_dir.glob("source.*"):
            leftover.unlink(missing_ok=True)

        status = "complete"
        if ocr_skipped_reason or not sample.timestamps_exact:
            status = "partial"
        visual_jobs.complete_visual_job(
            video_id, status=status, last_stage="ocr" if per_frame_text else "frames",
            profile=str(profile_result.value) if profile_result else job.get("profile"),
            db_path=db_path,
        )
        visual_jobs.log_visual_attempt(
            video_id, profile=job.get("profile"), provider="ffmpeg+ocr",
            outcome="ok", latency_ms=(time.monotonic() - started) * 1000,
            db_path=db_path,
        )
        receipt["result"] = {
            "ok": True,
            "status": status,
            "profile": str(profile_result.value) if profile_result else None,
            "artifact_bytes": _dir_bytes(video_dir),
            "publish": publish,
        }
    except frame_sampler.FrameExtractionError as exc:
        visual_jobs.fail_visual_job(
            video_id, error_class="frame_extraction",
            failure_reason=str(exc)[:500], retry_after_s=60.0, db_path=db_path,
        )
        visual_jobs.log_visual_attempt(
            video_id, profile=job.get("profile"), provider="ffmpeg+ocr",
            outcome="frame_extraction_failed",
            latency_ms=(time.monotonic() - started) * 1000,
            error_class="frame_extraction", db_path=db_path,
        )
        receipt["result"] = {"ok": False, "error_class": "frame_extraction", "error": str(exc)[:500]}
    receipt["finished_at"] = _utcnow_iso()
    receipt["elapsed_s"] = round(time.monotonic() - started, 3)
    return receipt


def main(argv: list[str] | None = None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--max-jobs", type=int, default=50)
    parser.add_argument("--max-runtime-s", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument(
        "--drain-backlog-n", type=int, default=25,
        help="deferred-audio drain items after the job loop (0 disables)",
    )
    parser.add_argument(
        "--drain-max-runtime-s", type=float, default=3600,
        help="wall-clock cap for the embedded drain phase",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--output-root", type=Path, default=None,
        help="Receipt root (default: <package .logs>/visual/<run-id>)",
    )
    args = parser.parse_args(argv)

    db_path = args.db_path or get_batch_db_path()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("visual-%Y%m%dT%H%M%SZ")
    run_root = args.output_root or (REPO_ROOT / ".logs" / "visual" / run_id)
    run_root.mkdir(parents=True, exist_ok=True)

    migration = run_v3_visual_queue_migration(db_path)

    summary: dict = {
        "run_id": run_id,
        "started_at": _utcnow_iso(),
        "db_path": str(db_path),
        "migration": migration,
        "dry_run": args.dry_run,
        "jobs_processed": 0,
        "jobs_complete": 0,
        "jobs_partial": 0,
        "jobs_failed": 0,
        "stop_reason": "completed",
        "jobs": [],
    }

    cooldown = media_fetch.media_cooldown_state(db_path)
    budget = media_fetch.budget_state(db_path)
    queue = visual_jobs.visual_queue_stats(db_path)
    summary["initial_state"] = {"cooldown": cooldown, "budget": budget, "queue": queue}

    if args.dry_run:
        summary["stop_reason"] = "dry_run"
        _write_json(run_root / "summary.json", summary)
        print(json.dumps(summary, indent=1, default=str))
        return 0

    if cooldown["active"]:
        summary["stop_reason"] = f"rate_limit_cooldown:{cooldown['reason']}"
        _write_json(run_root / "summary.json", summary)
        print(json.dumps(summary, indent=1, default=str))
        return 1

    started_mono = time.monotonic()
    while summary["jobs_processed"] < args.max_jobs:
        if args.max_runtime_s and (time.monotonic() - started_mono) > args.max_runtime_s:
            summary["stop_reason"] = "max_runtime"
            break
        if media_fetch.media_cooldown_state(db_path)["active"]:
            summary["stop_reason"] = "rate_limit_cooldown"
            break
        if not media_fetch.budget_state(db_path)["allowed"]:
            summary["stop_reason"] = "budget_exhausted"
            break

        job = visual_jobs.claim_next_visual_job(db_path)
        if job is None:
            summary["stop_reason"] = "queue_empty"
            break

        receipt = process_one(
            job, db_path=db_path, run_root=run_root, run_id=run_id, skip_ocr=args.skip_ocr
        )
        _write_json(run_root / "jobs" / f"{job['video_id']}.json", receipt)
        summary["jobs"].append({"video_id": job["video_id"], "result": receipt.get("result", {})})
        summary["jobs_processed"] += 1
        # Live progress for status/monitor: written per job so an idle-looking
        # paced run is distinguishable from a dead one.
        _write_json(
            run_root / "progress.json",
            {
                "run_id": run_id,
                "jobs_done": summary["jobs_processed"],
                "jobs_target": args.max_jobs,
                "complete": summary["jobs_complete"],
                "partial": summary["jobs_partial"],
                "failed": summary["jobs_failed"],
                "last_video": job["video_id"],
                "updated_at": _utcnow_iso(),
            },
        )
        result = receipt.get("result", {})
        if result.get("ok"):
            summary["jobs_complete" if result.get("status") == "complete" else "jobs_partial"] += 1
        else:
            summary["jobs_failed"] += 1
            if result.get("error_class") in {
                "rate_limited", "budget_exhausted", "cookie_source",
            }:
                summary["stop_reason"] = f"download_{result['error_class']}"
                break

        time.sleep(random.uniform(2.0, 10.0))

    summary["finished_at"] = _utcnow_iso()
    summary["final_queue"] = visual_jobs.visual_queue_stats(db_path)
    summary["media_root_bytes"] = _dir_bytes(media_fetch.media_root(db_path))
    if not args.dry_run and args.drain_backlog_n > 0:
        # Deferred-audio drain: backlog items never enter the job queue,
        # so the per-job eviction pass never sees them. Drain a bounded
        # batch through the feeder lifecycle every normal run (lazy
        # import: the feeder imports this module's deletion helpers).
        from scripts.deferred_audio_feeder import run_drain_phase
        summary["drain"] = run_drain_phase(
            args.drain_backlog_n,
            max_runtime_s=args.drain_max_runtime_s,
        )
    _write_json(run_root / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "jobs"}, indent=1, default=str))
    return 0 if summary["jobs_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
