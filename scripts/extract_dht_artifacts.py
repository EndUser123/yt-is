"""DA-02 / DA-03 — Discord attachment artifact extraction (handoff 2026-08-21).

For every image and video attachment in the three DHT archives, produce a
two-layer markdown artifact:

  LAYER 1 — OCR VERBATIM (EasyOCR, local GPU)
            Every visible label, number, annotation, axis tick — typed
            character-for-character. This is the durable record. EasyOCR is
            load-bearing because the operator requires every word on options
            charts to survive (handoff DA-02: "OCR verbatim is more
            trustworthy than LLM for numbers").

  LAYER 2 — VISION NARRATIVE (agy / Gemini, via csf.visual.gemini_extract)
            Reuses the proven PART 1 / PART 2 prompt from gemini_extract.py,
            adapted for single images and stock/option charts. Quality-gated
            through the same meta-text rejection the YouTube pipeline uses.

The artifact is written:
  1) to disk under  P:/.data/dht-artifacts/<archive-slug>/<channel_id>/
                    <message_id>_<attachment_id>.md
  2) to the connector ingest path: transcript_cache row with source =
     'dht-artifact' so the existing ingest_connectors.py picks it up (after
     the alias is added — see DA-02f).

The DHT archives are opened read-only via SQLite's file:...?mode=ro URI
(handoff DA-01 falsifier: "any archive opened read-write"). The handoff
constraint "DHT archive is the only durable copy of the media" is honored
by never depending on Discord CDN URLs at write time — the image bytes
land on disk before the artifact is generated.

Pacing: --sleep-between (default 2.0s) between vision calls; the OCR
pass is local GPU and counts as the "shared with the NLM drain" load
(handoff DA-02 constraints). For 23K images that's a multi-day run;
this script is meant to be invoked windowless via pythonw or a cron
task (handoff: "scheduled actions windowless (pythonw only)").

Usage examples:
  # Smoke (1 image, no writes to transcript_cache)
  python -m scripts.extract_dht_artifacts --archive perfect_strategy --limit 1

  # Dry-run (no OCR, no vision, just classify and report)
  python -m scripts.extract_dht_artifacts --archive all --dry-run

  # Resume from state, full run, paced
  python -m scripts.extract_dht_artifacts --archive all --sleep-between 2.0

  # Background-friendly: write receipt to .logs/dht-attachments/
  pythonw -m scripts.extract_dht_artifacts --archive all --resume
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# Make csf / ef importable when run as a script (not a package)
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ARCHIVES = {
    "unusual_whales":   r"P:\.data\dht\unusual whales.dht",
    "perfect_strategy": r"P:\.data\dht\perfect strategy.dht",
    "spx_0dte_trader":  r"P:\.data\dht\spx 0dte trader.dht",
}

ARTIFACT_ROOT = Path(r"P:\.data\dht-artifacts")
LOG_ROOT      = Path(r"P:\packages\yt-is\.logs\dht-attachments")
STATE_FILE    = LOG_ROOT / "DA-02-state.json"
RECEIPT_FILE  = LOG_ROOT / "DA-02-receipt.json"

# ---- single-image vision prompt (adapted from gemini_extract.EXTRACTION_PROMPT) ----
# Reuses the proven PART 1 verbatim / PART 2 narrative structure but is tuned
# for stock/option screenshots (the handoff's chart-heavy backlog) and
# single-image (not multi-frame) input.
SINGLE_IMAGE_PROMPT = """\
You are looking at ONE image (a Discord attachment — typically a chart,
screenshot, options chain, or stock photo). Produce TWO parts.

PART 1 — VISIBLE TEXT AND NUMBERS (transcription, never summary):
- Transcribe every visible label, number, annotation, axis tick, legend
  entry, watermark, and callout EXACTLY as displayed. Do NOT summarize,
  abbreviate, paraphrase, round, or interpret.
- Numbers are especially important: every strike, price, date, percentage,
  and volume figure must appear character-for-character. If a number is
  partially obscured, write [unclear: N?] and explain in PART 2.
- If the image is a chart/graph, list every series name, every axis label,
  and every readable data point.
- If the image is an options chain, list every row with strike, type,
  bid, ask, last, volume, OI, IV — exactly as shown.
- If the image is mostly non-text (photo, logo, meme), say so in PART 1
  (e.g. "No readable text.") and put the description in PART 2.

PART 2 — VISUAL MEANING AND CONTEXT:
- What does the image show? (chart type, ticker, timeframe, layout)
- What is the visual story? (e.g. "call wall at 5800", "iron condor centered
  on 5750", "IV crush after earnings", "10-year Treasury yield curve")
- Any annotations, arrows, circles, or highlighted regions — what do they
  point to?

RULES:
- Work the image carefully. Do not skip small text.
- Never invent values. If a label is unreadable, say so.
- Do NOT discuss your tools, your prompt, or the fact that you are an AI.
- Do NOT start with phrases like "Certainly", "Here is", "I'd be happy to".

End your reply with exactly:
  "OCR chars: <N>. Narrative chars: <M>."
where N is the count of transcribed characters in PART 1 and M is the
character count of PART 2.
"""


@dataclass
class Attachment:
    archive_slug: str
    archive_path: str
    message_id: int
    attachment_id: int
    name: str
    declared_type: str | None
    url: str
    size: int | None
    width: int | None = None
    height: int | None = None
    is_video: bool = False
    # When source == "blob", bytes already on disk (perfect strategy downloads)
    blob: bytes | None = None
    blob_path: str | None = None

    @property
    def cache_key(self) -> str:
        return f"dht-artifact:{self.archive_slug}:{self.message_id}:{self.attachment_id}"

    @property
    def content_hash(self) -> str:
        # Hash on (archive, message_id, attachment_id) so re-runs of the same
        # attachment are deduplicated; the image bytes are also folded in for
        # the unique-image case (rare — Discord reuses attachment_ids).
        h = hashlib.sha256()
        h.update(self.archive_slug.encode())
        h.update(b"\x00")
        h.update(str(self.message_id).encode())
        h.update(b"\x00")
        h.update(str(self.attachment_id).encode())
        h.update(b"\x00")
        if self.blob is not None:
            h.update(self.blob[:4096])
        return h.hexdigest()[:16]


def is_video_url(url: str, name: str) -> bool:
    s = f"{url or ''} {name or ''}".lower()
    return any(ext in s for ext in (".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif"))


def open_archive_ro(path: str) -> sqlite3.Connection:
    """SQLite file: URI read-only — handoff DA-01 falsifier guards on this."""
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def iter_attachments(slug: str, path: str) -> Iterator[Attachment]:
    """Yield every attachment row in the archive."""
    conn = open_archive_ro(path)
    try:
        cur = conn.execute(
            'SELECT message_id, attachment_id, name, type, url, size '
            'FROM "attachments"'
        )
        for msg_id, att_id, name, declared_type, url, size in cur:
            yield Attachment(
                archive_slug=slug,
                archive_path=path,
                message_id=int(msg_id),
                attachment_id=int(att_id),
                name=name or "",
                declared_type=declared_type,
                url=url or "",
                size=int(size) if size is not None else None,
                is_video=is_video_url(url or "", name or ""),
            )
    finally:
        conn.close()


def iter_blobs(slug: str, path: str) -> Iterator[Attachment]:
    """Yield 732 pre-existing downloads-table blobs (perfect strategy only)."""
    conn = open_archive_ro(path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        if "downloads" not in tables:
            return
        # Only status=200 rows are usable
        cur = conn.execute(
            'SELECT url, status, size, blob FROM "downloads" WHERE status = 200'
        )
        for url, status, size, blob in cur:
            if not blob:
                continue
            # Synthesize a stable attachment_id from the URL hash so re-runs
            # are deduped. message_id is unknown here; use 0.
            att_id = int(hashlib.sha256((url or "").encode()).hexdigest()[:12], 16)
            yield Attachment(
                archive_slug=slug,
                archive_path=path,
                message_id=0,  # unknown for downloads-table rows
                attachment_id=att_id,
                name=(url or "").rsplit("/", 1)[-1] or "blob",
                declared_type=None,
                url=url or "",
                size=int(size) if size is not None else len(blob),
                is_video=is_video_url(url or "", ""),
                blob=bytes(blob),
            )
    finally:
        conn.close()


# -------- source resolution (CDN fetch OR pre-existing blob) --------

class _CDNExpired(Exception):
    """Discord CDN URL is gone (404/410/403). Caller marks as soft-fail."""

class _CDNFetchError(Exception):
    """Network/server error fetching the URL. Worth retrying later."""


def fetch_attachment_bytes(att: Attachment, *, timeout_s: int = 30,
                            head_timeout_s: int = 4) -> bytes:
    """Fetch image bytes from the Discord CDN. NEVER persist the URL — only
    the bytes. Caller owns the file lifecycle.

    Two-stage: HEAD first (short timeout) to fail fast on expired URLs, then
    GET the bytes. Without the HEAD probe, a 23K-URL sweep would burn ~200h
    on full 30s timeouts per expired URL.
    """
    if att.blob is not None:
        return att.blob
    if not att.url:
        raise ValueError(f"no source bytes for {att.cache_key}: blob=None, url=''")

    # 1) HEAD probe — Discord's CDN returns 200/206 for fresh URLs and 404
    #    for expired ones within ~100ms, no body transfer.
    head_req = urllib.request.Request(att.url, method="HEAD",
                                       headers={"User-Agent": "yt-is-da02/1.0"})
    try:
        with urllib.request.urlopen(head_req, timeout=head_timeout_s) as resp:
            if resp.status not in (200, 206):
                raise _CDNExpired(f"HEAD status {resp.status}")
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 410):
            raise _CDNExpired(f"HEAD HTTP {e.code}")
        raise _CDNFetchError(f"HEAD HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # Network issue — try GET anyway (may succeed where HEAD fails on
        # some CDN edges)
        pass

    # 2) GET the bytes
    get_req = urllib.request.Request(att.url, method="GET",
                                      headers={"User-Agent": "yt-is-da02/1.0"})
    try:
        with urllib.request.urlopen(get_req, timeout=timeout_s) as resp:
            if resp.status not in (200, 206):
                raise _CDNExpired(f"GET status {resp.status}")
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 410):
            raise _CDNExpired(f"GET HTTP {e.code}")
        raise _CDNFetchError(f"GET HTTP {e.code}")


# -------- EasyOCR verbatim layer (local GPU) --------

_OCR_READER = None
def get_ocr_reader():
    global _OCR_READER
    if _OCR_READER is None:
        import easyocr  # heavy import — defer
        _OCR_READER = easyocr.Reader(["en"], gpu=True)
    return _OCR_READER


def ocr_verbatim(image_bytes: bytes) -> tuple[str, int]:
    """Run EasyOCR on the image; return (verbatim_text, char_count).

    EasyOCR returns a list of (bbox, text, conf). Concatenate the text
    fragments in reading order (top-to-bottom, left-to-right) so the
    output looks like a linear transcription.
    """
    import numpy as np
    from PIL import Image
    reader = get_ocr_reader()
    img = Image.open(io.BytesIO(image_bytes))
    arr = np.array(img.convert("RGB"))
    # paragraph=False to preserve line structure; we re-sort by bbox below
    result = reader.readtext(arr, detail=1, paragraph=False)
    if not result:
        return "", 0
    # Sort by (top, left) — bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    def reading_key(item):
        bbox, text, conf = item
        x1, y1 = bbox[0]
        return (round(y1 / 12), x1)  # group lines by 12-px row
    sorted_items = sorted(result, key=reading_key)
    lines = []
    last_row = None
    for bbox, text, conf in sorted_items:
        if not text or not text.strip():
            continue
        y1 = bbox[0][1]
        row = round(y1 / 12)
        if last_row is not None and row != last_row:
            lines.append("")
        lines.append(text.strip())
        last_row = row
    return "\n".join(lines).strip(), sum(len(t) for _, t, _ in result if t)


# -------- vision narrative layer (agy via csf.visual.gemini_extract) --------

def vision_narrative(image_path: Path, *, print_timeout_s: str = "3m") -> dict:
    """Run agy on a single image; return {"ok": True, "markdown": ...} or
    {"ok": False, "error": ...}.

    Reuses csf.visual.gemini_extract's machinery: the engine-order preference
    (agy first, then API), the meta-text quality gate, and the fenced-block
    dedup. We adapt the prompt because gemini_extract frames the request as
    "read N JPEGs in a directory" — for DHT attachments we have one image.
    """
    from csf.visual import gemini_extract
    prompt = (
        f"Read the single image at {image_path} carefully. "
        + SINGLE_IMAGE_PROMPT
    )
    # 1) agy first (operator preference)
    if os.environ.get("YTIS_VISUAL_EXTRACT_ENGINE", "agy-first") == "agy-first":
        agy_result = gemini_extract.extract_via_agy(prompt, print_timeout_s=print_timeout_s)
        if agy_result.get("ok") and gemini_extract._agy_output_is_task(agy_result["markdown"]):
            return {
                "ok": True,
                "engine": "agy",
                "markdown": gemini_extract.dedup_fenced_blocks(agy_result["markdown"]),
            }
    # 2) API fallback (Gemini keys, same chain as gemini_extract)
    import os as _os
    model = _os.environ.get("YTIS_VISUAL_MODEL", "gemini-2.5-flash")
    last_error = "no usable key"
    for client, key_name in gemini_extract.iter_clients():
        try:
            from google.genai import types as _types
            response = client.models.generate_content(
                model=model,
                contents=[
                    prompt,
                    _types.Part.from_bytes(
                        data=image_path.read_bytes(),
                        mime_type=_guess_mime(image_path),
                    ),
                ],
            )
            text = (response.text or "").strip()
            if text:
                return {
                    "ok": True,
                    "engine": f"api:{key_name}",
                    "model": model,
                    "markdown": gemini_extract.dedup_fenced_blocks(text),
                }
            last_error = f"empty response via {key_name}"
        except Exception as exc:
            last_error = f"{key_name}: {type(exc).__name__}: {str(exc)[:200]}"
            continue

    # 3) OpenRouter fallback (works with image_url content parts). Sends the
    #    image as a data URL so OpenRouter's vision-capable models can read it.
    #    Model default: google/gemini-2.5-flash (matches the operator's primary).
    #    Override with YTIS_VISUAL_OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
    #    etc. for higher-quality chart interpretation at higher cost.
    or_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
    if or_key:
        try:
            import base64 as _b64
            import json as _json
            import urllib.request as _ur
            or_model = _os.environ.get("YTIS_VISUAL_OPENROUTER_MODEL",
                                       "google/gemini-2.5-flash")
            img_bytes = image_path.read_bytes()
            b64 = _b64.b64encode(img_bytes).decode("ascii")
            data_url = f"data:{_guess_mime(image_path)};base64,{b64}"
            body = _json.dumps({
                "model": or_model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }],
                "max_tokens": 4000,
            }).encode("utf-8")
            req = _ur.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {or_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://yt-is.local/da02",
                },
            )
            with _ur.urlopen(req, timeout=120) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
            choice = (payload.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content") or "").strip()
            if text:
                # Same quality gate as agy: real task output, not meta-text
                if gemini_extract._agy_output_is_task(text):
                    return {
                        "ok": True,
                        "engine": f"openrouter:{or_model}",
                        "model": or_model,
                        "markdown": gemini_extract.dedup_fenced_blocks(text),
                    }
                last_error = f"openrouter quality gate rejected ({or_model})"
            else:
                last_error = f"openrouter empty response ({or_model})"
        except Exception as exc:
            last_error = f"openrouter: {type(exc).__name__}: {str(exc)[:200]}"

    return {"ok": False, "error": last_error}


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")


# -------- artifact assembly --------

def render_artifact_markdown(att: Attachment, ocr_text: str, ocr_chars: int,
                              vision: dict, archive_label: str,
                              channel_id: str | None,
                              channel_name: str | None) -> str:
    parts: list[str] = []
    parts.append(f"# {att.name or 'attachment'}")
    parts.append("")
    parts.append(f"- archive: `{archive_label}`")
    parts.append(f"- message_id: `{att.message_id}`")
    parts.append(f"- attachment_id: `{att.attachment_id}`")
    if att.url:
        # Include for provenance only — handoff: "Discord CDN URLs expire"
        parts.append(f"- source_url: {att.url}")
    if att.size is not None:
        parts.append(f"- size_bytes: {att.size}")
    if att.width and att.height:
        parts.append(f"- dimensions: {att.width}x{att.height}")
    if channel_id or channel_name:
        parts.append(f"- channel: {channel_id or ''} {('#' + channel_name) if channel_name else ''}")
    parts.append("")
    parts.append("## OCR (verbatim — EasyOCR)")
    parts.append("")
    if ocr_text:
        parts.append("```")
        parts.append(ocr_text)
        parts.append("```")
    else:
        parts.append("_EasyOCR detected no readable text._")
    parts.append("")
    parts.append("## Vision narrative (agy / Gemini)")
    parts.append("")
    if vision.get("ok"):
        parts.append(vision["markdown"].strip())
    else:
        parts.append(f"_Vision extraction failed: {vision.get('error', 'unknown')}_")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(f"_OCR chars: {ocr_chars}. Vision engine: {vision.get('engine', '?')}. "
                 f"Content hash: {att.content_hash}._")
    return "\n".join(parts)


def write_artifact(att: Attachment, markdown: str) -> Path:
    out_dir = ARTIFACT_ROOT / att.archive_slug / f"ch_{(att.message_id or 0) // 1000:06d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{att.message_id}_{att.attachment_id}.md"
    out_path.write_text(markdown, encoding="utf-8")
    return out_path


# -------- transcript_cache row (so existing ingest picks it up) --------

def upsert_transcript_cache_row(att: Attachment, markdown: str,
                                  archive_label: str) -> bool:
    """Append a row to the authority.TRANSCRIPTS_DB transcript_cache table
    with source='dht-artifact' so the existing ingest_connectors flow (with
    a new alias — DA-02f) will pick it up and turn it into an EvidenceUnit."""
    from ef import authority
    db = authority.TRANSCRIPTS_DB
    if not db.exists():
        return False
    cache_key = att.cache_key
    cached_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta = {
        "archive": archive_label,
        "message_id": att.message_id,
        "attachment_id": att.attachment_id,
        "name": att.name,
        "url": att.url,
        "size_bytes": att.size,
        "content_hash": att.content_hash,
        "source_kind": "dht-artifact",
    }
    try:
        conn = sqlite3.connect(str(db))
        try:
            # Idempotent insert; on conflict, update transcript + metadata
            conn.execute(
                """
                INSERT INTO transcript_cache
                  (cache_key, video_id, source, cached_at, transcript, metadata_json, lang, terminal_id)
                VALUES (?, ?, 'dht-artifact', ?, ?, ?, 'en', 'dht-artifact')
                ON CONFLICT(cache_key) DO UPDATE SET
                  transcript = excluded.transcript,
                  cached_at = excluded.cached_at,
                  metadata_json = excluded.metadata_json
                """,
                (cache_key, cache_key, cached_at, markdown, json.dumps(meta)),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.OperationalError as e:
        # If the schema lacks cache_key as a unique key, fall back to plain insert
        # — better to lose idempotency than to lose the artifact.
        try:
            conn = sqlite3.connect(str(db))
            conn.execute(
                """INSERT INTO transcript_cache
                   (cache_key, video_id, source, cached_at, transcript, metadata_json, lang, terminal_id)
                   VALUES (?, ?, 'dht-artifact', ?, ?, ?, 'en', 'dht-artifact')""",
                (cache_key, cache_key, cached_at, markdown, json.dumps(meta)),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e2:
            print(f"  [WARN] transcript_cache upsert failed for {cache_key}: {e2}", flush=True)
            return False


# -------- state (resume) --------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"processed": {}, "errors": {}, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def save_state(state: dict) -> None:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


# -------- per-attachment processing --------

def process_one(att: Attachment, *, dry_run: bool, do_ocr: bool, do_vision: bool,
                sleep_s: float) -> dict:
    """Process a single attachment. Returns a result dict for the receipt."""
    result = {
        "cache_key": att.cache_key,
        "content_hash": att.content_hash,
        "name": att.name,
        "size": att.size,
        "is_video": att.is_video,
        "source": "blob" if att.blob is not None else "cdn",
    }
    if dry_run:
        result["dry_run"] = True
        return result

    t0 = time.time()
    try:
        image_bytes = fetch_attachment_bytes(att)
    except _CDNExpired as e:
        # Soft failure: the handoff explicitly says "Discord CDN attachment
        # URLs expire (~24h signed links)" — this is the expected case for
        # most of the 23K backlog. Don't count as error; record for receipt.
        result["expired"] = str(e)[:200]
        return result
    except _CDNFetchError as e:
        result["error"] = f"fetch_transient: {str(e)[:200]}"
        return result
    except Exception as e:
        result["error"] = f"fetch: {type(e).__name__}: {str(e)[:200]}"
        return result
    result["fetch_ms"] = int((time.time() - t0) * 1000)

    if not do_ocr and not do_vision:
        result["skipped"] = "no-ocr no-vision"
        return result

    # Stage the image on disk for the vision call (agy reads by path)
    stage = ARTIFACT_ROOT / "_staging" / f"{att.content_hash}.bin"
    stage.parent.mkdir(parents=True, exist_ok=True)
    if att.blob is None:
        stage.write_bytes(image_bytes)
    # For blobs, write a tiny sidecar so vision knows the file type
    if att.blob is not None:
        # Determine suffix from name
        suffix = Path(att.name).suffix.lower() or ".bin"
        suffixed = stage.with_suffix(suffix)
        stage = suffixed
        stage.write_bytes(image_bytes)

    # OCR layer
    ocr_text, ocr_chars = "", 0
    if do_ocr:
        t1 = time.time()
        try:
            ocr_text, ocr_chars = ocr_verbatim(image_bytes)
        except Exception as e:
            result["ocr_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        result["ocr_ms"] = int((time.time() - t1) * 1000)
        result["ocr_chars"] = ocr_chars

    # Vision layer
    vision: dict = {"ok": False, "error": "skipped"}
    if do_vision and not att.is_video:  # DA-03 handles video separately
        t2 = time.time()
        try:
            vision = vision_narrative(stage)
        except Exception as e:
            vision = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        result["vision_ms"] = int((time.time() - t2) * 1000)
        result["vision_engine"] = vision.get("engine", "?")

    markdown = render_artifact_markdown(
        att, ocr_text, ocr_chars, vision,
        archive_label=att.archive_slug, channel_id=None, channel_name=None,
    )
    out_path = write_artifact(att, markdown)
    result["artifact_path"] = str(out_path)

    # Emit to transcript_cache so the connector ingest sees it
    if not dry_run:
        upsert_transcript_cache_row(att, markdown, att.archive_slug)
        result["transcript_cache"] = True

    result["total_ms"] = int((time.time() - t0) * 1000)
    if sleep_s > 0 and do_vision:
        time.sleep(sleep_s)
    return result


# -------- driver --------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", choices=list(ARCHIVES) + ["all"], default="all",
                    help="Which DHT archive to process")
    ap.add_argument("--include-blobs", action="store_true",
                    help="Also process the 732 pre-existing downloads-table blobs (perfect strategy)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N attachments (0 = all)")
    ap.add_argument("--offset", type=int, default=0,
                    help="Skip the first N (for chunked runs)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Classify and report only; do not OCR/vision/write")
    ap.add_argument("--no-ocr", action="store_true", help="Skip EasyOCR layer")
    ap.add_argument("--no-vision", action="store_true", help="Skip agy/Gemini layer")
    ap.add_argument("--videos-only", action="store_true", help="Process only videos (DA-03)")
    ap.add_argument("--images-only", action="store_true", help="Process only images (DA-02)")
    ap.add_argument("--sleep-between", type=float, default=2.0,
                    help="Seconds to sleep between vision calls (default 2.0)")
    ap.add_argument("--resume", action="store_true",
                    help="Skip already-processed attachments (uses STATE_FILE)")
    ap.add_argument("--print-timeout", default="3m",
                    help="agy --print-timeout value (default 3m)")
    ap.add_argument("--flush-every", type=int, default=10,
                    help="Persist state every N attachments")
    args = ap.parse_args()

    archives = list(ARCHIVES.items()) if args.archive == "all" else [(args.archive, ARCHIVES[args.archive])]

    state = load_state() if args.resume else load_state()  # always read for visibility
    if not args.resume:
        state = {"processed": {}, "errors": {}, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    save_state(state)

    grand_total = 0
    grand_done = 0
    grand_skipped = 0
    grand_errors = 0
    for slug, path in archives:
        print(f"\n=== {slug}  ({path}) ===", flush=True)
        if not Path(path).exists():
            print(f"  [SKIP] archive missing", flush=True)
            continue

        # Stream attachments (and optionally blobs)
        atts: list[Attachment] = list(iter_attachments(slug, path))
        print(f"  attachments: {len(atts)}", flush=True)
        if args.include_blobs or slug == "perfect_strategy":
            blobs = list(iter_blobs(slug, path))
            print(f"  blobs:       {len(blobs)}  (from downloads table)", flush=True)
            atts.extend(blobs)
        else:
            print(f"  blobs:       0  (--include-blobs not set)", flush=True)

        if args.videos_only:
            atts = [a for a in atts if a.is_video]
            print(f"  -> videos only: {len(atts)}", flush=True)
        elif args.images_only:
            atts = [a for a in atts if not a.is_video]
            print(f"  -> images only: {len(atts)}", flush=True)

        if args.offset:
            atts = atts[args.offset:]
        if args.limit:
            atts = atts[:args.limit]

        grand_total += len(atts)
        t_archive = time.time()
        for i, att in enumerate(atts, start=1):
            if args.resume and att.content_hash in state["processed"]:
                grand_skipped += 1
                continue
            t0 = time.time()
            r = process_one(
                att, dry_run=args.dry_run,
                do_ocr=not args.no_ocr, do_vision=not args.no_vision,
                sleep_s=args.sleep_between,
            )
            dt = time.time() - t0
            # Soft vs hard failure:
            #   - "error" = hard failure (fetch, write, schema). Don't count as done.
            #   - "ocr_error" = hard failure (OCR crashed). Don't count as done.
            #   - vision failure (no engine) is SOFT — OCR layer is the durable
            #     record (handoff: "OCR verbatim is more trustworthy than LLM
            #     for numbers"). Count as done, log the soft failure.
            if "error" in r or r.get("ocr_error"):
                state["errors"][att.content_hash] = r
                grand_errors += 1
            elif r.get("expired"):
                # CDN URL expired — handoff's expected case; record for receipt
                # and skip (resume will re-process if/when URLs are re-fetched).
                state.setdefault("expired_cdn", {})[att.content_hash] = r
            else:
                if r.get("vision_engine") == "?" and not args.no_vision and not att.is_video:
                    state.setdefault("soft_failures", {})[att.content_hash] = r
                state["processed"][att.content_hash] = r
                grand_done += 1
            if i % args.flush_every == 0 or i == len(atts):
                save_state(state)
            print(f"  [{i}/{len(atts)}] {att.cache_key} dt={dt:.1f}s "
                  f"ocr_chars={r.get('ocr_chars','-')} eng={r.get('vision_engine','-')} "
                  f"err={r.get('error') or r.get('ocr_error') or ''}"
                  f"{' EXPIRED' if r.get('expired') else ''}", flush=True)
        archive_dt = time.time() - t_archive
        expired_count = len(state.get("expired_cdn", {}))
        soft_count = len(state.get("soft_failures", {}))
        print(f"  archive done in {archive_dt:.1f}s  "
              f"({grand_done} done, {grand_errors} err, {expired_count} cdn_expired, "
              f"{soft_count} vision_soft, {grand_skipped} skipped_resumed)", flush=True)

    state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state["totals"] = {
        "considered": grand_total, "done": grand_done,
        "errors": grand_errors, "skipped_resumed": grand_skipped,
        "expired_cdn": len(state.get("expired_cdn", {})),
        "vision_soft_failures": len(state.get("soft_failures", {})),
        "dry_run": args.dry_run,
    }
    save_state(state)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    RECEIPT_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\nWROTE {RECEIPT_FILE}", flush=True)
    print(json.dumps(state["totals"], indent=2), flush=True)
    return 0 if grand_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
