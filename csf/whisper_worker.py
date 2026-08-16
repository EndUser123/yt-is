"""Process-isolated faster-whisper transcription worker.

The parent transcript pipeline launches this module for the CPU-heavy Whisper
stage so a per-video timeout can terminate the model process without leaving a
Python worker thread stuck forever.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def _write_json_result_atomically(path: Path, payload: dict[str, object]) -> None:
    """Publish a worker result only after the complete JSON is on disk."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=True, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _empty_result_error(segments: list[object]) -> str:
    no_speech_probs: list[float] = []
    for segment in segments:
        try:
            probability = getattr(segment, "no_speech_prob", None)
        except Exception:
            probability = None
        if probability is not None:
            try:
                no_speech_probs.append(float(probability))
            except (TypeError, ValueError):
                continue

    if no_speech_probs:
        maximum = max(no_speech_probs)
        if maximum >= 0.75:
            return (
                "whisper no speech detected (likely music or silence; "
                f"segments={len(segments)}, max_no_speech_prob={maximum:.2f})"
            )
        return (
            "whisper produced empty transcript "
            f"(segments={len(segments)}, max_no_speech_prob={maximum:.2f})"
        )
    return f"whisper produced empty transcript (segments={len(segments)})"


def transcribe(audio_path: Path, lang: str) -> dict[str, object]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"ok": False, "error": "faster-whisper not installed"}

    try:
        model = WhisperModel("medium", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(
            str(audio_path),
            language=lang if lang != "en" else None,
        )
        materialized_segments = list(segments)
        text = " ".join(segment.text for segment in materialized_segments).strip()
        if not text:
            return {"ok": False, "error": _empty_result_error(materialized_segments)}
        return {"ok": True, "transcript": text}
    except Exception as exc:
        return {"ok": False, "error": f"whisper transcription error: {exc}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-file", type=Path, required=True)
    parser.add_argument("--lang", required=True)
    parser.add_argument("--result-path", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = transcribe(args.audio_file, args.lang)
    _write_json_result_atomically(args.result_path, payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
