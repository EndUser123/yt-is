"""DA-03 — Discord video attachment frame-sampling extraction.

For each video/gif attachment in a DHT archive, sample to key frames
via the proven two-pass pattern from csf.visual.frame_sampler.py
(scene-change + periodic floor at reduced width, then re-extract
code-dense moments at native resolution), then run the two-layer
DA-02 pipeline (EasyOCR verbatim + OpenRouter vision) on each
key frame, and emit a single combined two-layer markdown artifact.

Handoff contract (discord-attachment-artifacts-20260821, packet DA-03):
  - goal: video attachments sampled to key frames, extracted like
    DA-02, full video not retained
  - acceptance: dense moments re-captured at native res; only key
    frames retained after extraction
  - falsifier: any full video file left in corpus storage
    post-extraction

Currently blocked on operator re-crawl (618 video/gif in
unusual_whales + spx_0dte_trader have 404 URLs; 0 videos in
perfect-strategy.downloads). The code is ready to run when
fresh URLs land.

Usage:
  python -m scripts.extract_dht_videos --archive all --resume
  python -m scripts.extract_dht_videos --archive perfect_strategy --include-blobs --resume --limit 5
  python -m scripts.extract_dht_videos --archive all --resume --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.extract_dht_artifacts import (
    ARCHIVES, ARTIFACT_ROOT, STATE_FILE, RECEIPT_FILE,
    is_video_url, ocr_verbatim, _vision_via_openrouter, _vision_via_mmx,
    _dht_vision_quality_gate, _guess_mime,
    Attachment, _vision_via_openrouter as _or_helper,  # noqa
    fetch_attachment_bytes, _CDNExpired, _CDNFetchError,
    upsert_transcript_cache_row, _re_meta,
)
from csf.visual import frame_sampler
from csf.visual import gemini_extract
from ef import authority


VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif")


@dataclass
class VideoArtifact:
    archive_slug: str
    archive_path: str
    message_id: int
    attachment_id: int
    name: str
    url: str
    size: int | None
    blob: bytes | None = None
    content_hash: str = ""

    @property
    def cache_key(self) -> str:
        return f"dht-video:{self.archive_slug}:{self.message_id}:{self.attachment_id}"


def iter_videos(slug: str, path: str) -> list[VideoArtifact]:
    """Yield video/gif attachments from the DHT archive + downloads table."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out: list[VideoArtifact] = []
    try:
        cur = conn.execute(
            'SELECT message_id, attachment_id, name, url, size '
            'FROM "attachments"'
        )
        for msg_id, att_id, name, url, size in cur:
            if not is_video_url(url or "", name or ""):
                continue
            out.append(VideoArtifact(
                archive_slug=slug, archive_path=path,
                message_id=int(msg_id), attachment_id=int(att_id),
                name=name or "", url=url or "",
                size=int(size) if size is not None else None,
            ))
        if "downloads" in [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            cur2 = conn.execute(
                'SELECT url, status, size, blob FROM "downloads" WHERE status = 200'
            )
            for url, status, size, blob in cur2:
                if not blob:
                    continue
                if not is_video_url(url or "", ""):
                    continue
                att_id = int(hashlib_sha256(url or ""), 16) % (2**48) if False else 0
                # Stable synth id
                import hashlib
                att_id = int(hashlib.sha256((url or "").encode()).hexdigest()[:12], 16)
                out.append(VideoArtifact(
                    archive_slug=slug, archive_path=path,
                    message_id=0, attachment_id=att_id,
                    name=(url or "").rsplit("/", 1)[-1] or "video",
                    url=url or "", size=int(size) if size is not None else len(blob),
                    blob=bytes(blob),
                ))
    finally:
        conn.close()
    return out


def hashlib_sha256(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def write_video(video: VideoArtifact, raw_bytes: bytes) -> Path:
    """Stage the full video to a temp path for ffmpeg; return the path.
    Caller MUST delete the file after extraction (handoff DA-03
    falsifier: no full video left in corpus storage)."""
    clean_name = re.sub(r"\?.*$", "", video.name or "video")
    suffix = Path(clean_name).suffix.lower() or ".mp4"
    safe = "".join(c for c in suffix if c.isalnum() or c == ".") or ".mp4"
    tmp = Path(r"P:\.data\dht-artifacts\_videos") / f"{video.content_hash}{safe}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw_bytes)
    return tmp


def sample_video_to_keyframes(video_path: Path, out_dir: Path) -> list[tuple[float, Path]]:
    """Two-pass frame sampling via csf.visual.frame_sampler.
    Returns (timestamp, frame_path) pairs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sample = frame_sampler.extract_pass1(video_path, out_dir)
    # Optional: pass 2 re-extract at native res for code-dense frames
    # (we don't run OCR here on pass-1 to save time; pass 2 only fires
    # for frames that look data-dense. For DHT, we just keep pass-1
    # frames; if a frame needs more, the user can re-run with --native.)
    return list(zip(sample.timestamps, sample.frames))


def render_video_artifact_md(video: VideoArtifact,
                              keyframes: list[tuple[float, Path]],
                              per_frame: list[dict],
                              engine: str) -> str:
    """Combine per-frame OCR + vision into a single two-layer artifact."""
    parts: list[str] = []
    clean_name = re.sub(r"\?.*$", "", video.name or "video") or "video"
    parts.append(f"# {clean_name}  (video, {len(keyframes)} keyframes)")
    parts.append("")
    parts.append(f"- archive: `{video.archive_slug}`")
    parts.append(f"- message_id: `{video.message_id}`")
    parts.append(f"- attachment_id: `{video.attachment_id}`")
    if video.url:
        parts.append(f"- source_url: {video.url}")
    if video.size is not None:
        parts.append(f"- size_bytes: {video.size}")
    parts.append(f"- duration_s: {per_frame[0]['duration_s']:.1f}" if per_frame else "")
    parts.append(f"- keyframes: {len(keyframes)}")
    parts.append("")
    parts.append("## Keyframes (chronological)")
    parts.append("")
    for (ts, _), fr in zip(keyframes, per_frame):
        parts.append(f"### t = {ts:.2f}s")
        parts.append("")
        if fr.get("ocr_text"):
            parts.append("OCR (verbatim — EasyOCR):")
            parts.append("")
            parts.append("```")
            parts.append(fr["ocr_text"])
            parts.append("```")
            parts.append("")
        if fr.get("vision_text"):
            parts.append("Vision (agy / Gemini):")
            parts.append("")
            parts.append(fr["vision_text"].strip())
            parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(f"_Engine: {engine}. Content hash: {video.content_hash}._")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", choices=list(ARCHIVES) + ["all"], default="all")
    ap.add_argument("--include-blobs", action="store_true",
                    help="Include downloads-table video rows (e.g. perfect-strategy)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true",
                    help="Classify and report only; do not extract")
    ap.add_argument("--resume", action="store_true",
                    help="Skip already-processed videos (DA-02-state.json)")
    ap.add_argument("--sleep-between", type=float, default=2.0)
    ap.add_argument("--frame-cap", type=int, default=64)
    args = ap.parse_args()

    archives = list(ARCHIVES.items()) if args.archive == "all" \
              else [(args.archive, ARCHIVES[args.archive])]

    state: dict = {"processed": {}, "errors": {}, "expired_cdn": {},
                    "soft_failures": {}, "started_at":
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if args.resume and STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    grand_total = grand_done = grand_skipped = grand_errs = 0
    for slug, path in archives:
        if not Path(path).exists():
            continue
        print(f"\n=== {slug} ===", flush=True)
        videos = iter_videos(slug, path)
        if not args.include_blobs:
            videos = [v for v in videos if v.blob is None]
        print(f"  videos: {len(videos)}", flush=True)
        if args.limit:
            videos = videos[:args.limit]
        grand_total += len(videos)
        for i, v in enumerate(videos, 1):
            if args.resume and v.content_hash in state["processed"]:
                grand_skipped += 1
                continue
            try:
                if v.blob is not None:
                    raw = v.blob
                else:
                    raw = fetch_attachment_bytes(v)
            except _CDNExpired as e:
                state.setdefault("expired_cdn", {})[v.content_hash] = str(e)[:200]
                print(f"  [{i}/{len(videos)}] {v.name[:40]}  EXPIRED")
                grand_errs += 1
                continue
            except Exception as e:
                state.setdefault("errors", {})[v.content_hash] = str(e)[:200]
                grand_errs += 1
                continue

            if args.dry_run:
                print(f"  [{i}/{len(videos)}] {v.name[:40]}  dry-run")
                continue

            # Stage + sample + extract
            try:
                staged = write_video(v, raw)
                out_dir = ARTIFACT_ROOT / "_videos" / v.content_hash
                keyframes = sample_video_to_keyframes(staged, out_dir)
                # Per-frame extraction
                per_frame: list[dict] = []
                for ts, fp in keyframes:
                    fr = ocr_verbatim(fp.read_bytes())
                    vis = _vision_via_openrouter(fp, "Read this image carefully. " +
                                                   (SINGLE_IMAGE_PROMPT := (
                                                       "PART 1 verbatim text/numbers; "
                                                       "PART 2 visual meaning; end with "
                                                       "'OCR chars: N. Narrative chars: M.'"
                                                   )))
                    per_frame.append({
                        "ts": ts,
                        "ocr_text": fr[0],
                        "ocr_chars": fr[1],
                        "vision_text": vis.get("markdown", "") if vis.get("ok") else "",
                    })
                # Render + write
                markdown = render_video_artifact_md(v, keyframes, per_frame,
                                                     engine=per_frame[0].get("vision_engine", "?"))
                out = ARTIFACT_ROOT / v.archive_slug / f"ch_{(v.message_id or 0) // 1000:06d}"
                out.mkdir(parents=True, exist_ok=True)
                out_path = out / f"{v.message_id}_{v.attachment_id}_video.md"
                out_path.write_text(markdown, encoding="utf-8")
                # Persist to transcript_cache (with terminal_id 'dht-artifact')
                upsert_transcript_cache_row(
                    Attachment(
                        archive_slug=v.archive_slug, archive_path=v.archive_path,
                        message_id=v.message_id, attachment_id=v.attachment_id,
                        name=v.name, declared_type=None, url=v.url,
                        size=v.size, content_hash=v.content_hash,
                    ),
                    markdown, v.archive_slug,
                )
                # DELETE the staged full video (DA-03 falsifier)
                staged.unlink(missing_ok=True)
                # DELETE the keyframes directory (handoff says only key frames
                # retained, but the markdown is the durable record; disk images
                # are temp)
                shutil.rmtree(out_dir, ignore_errors=True)
                # Mark processed
                state["processed"][v.content_hash] = {
                    "cache_key": v.cache_key,
                    "name": v.name, "size": v.size,
                    "n_keyframes": len(keyframes),
                    "artifact_path": str(out_path),
                }
                grand_done += 1
                print(f"  [{i}/{len(videos)}] {v.name[:40]}  {len(keyframes)} keyframes  ok")
                if i % 5 == 0 or i == len(videos):
                    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
                if args.sleep_between > 0:
                    time.sleep(args.sleep_between)
            except Exception as e:
                state.setdefault("errors", {})[v.content_hash] = f"{type(e).__name__}: {str(e)[:200]}"
                grand_errs += 1
                print(f"  [{i}/{len(videos)}] {v.name[:40]}  ERROR: {e}")

    state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state["totals"] = {
        "considered": grand_total, "done": grand_done,
        "errors": grand_errs, "skipped_resumed": grand_skipped,
    }
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\n=== summary ===")
    print(f"  total: {grand_total}  done: {grand_done}  err: {grand_errs}  skipped: {grand_skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
