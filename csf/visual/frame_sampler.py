"""Two-pass ffmpeg frame sampling for the visual pipeline (2026-08-18).

Pass 1 extracts scene-change keyframes plus a periodic coverage floor at a
reduced width (640px default) with honest per-frame timestamps parsed from
ffmpeg ``showinfo``. Pass 2 re-captures OCR-flagged moments at native
resolution while the video file is still local — the over-image policy: pay
bandwidth once, keep every frame worth keeping, never re-download to re-read.

This replaces the shipped U-03 crv adapter, which returned an empty frame
list on every successful crv run (its MANIFEST parser expected tab-separated
rows crv never writes), globbed the wrong fallback directory, fabricated
fallback timestamps, and leaked temp dirs. Using ffmpeg's scene-select filter
directly preserves DEC-03's intent — no new scene-detection dependency —
while fixing fidelity (crv hardcodes ``scale=640``) and timestamp honesty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import subprocess

from csf.providers import NonFatalAnalysisError

_DEFAULTS = {
    "scene_threshold": 0.30,
    "floor_interval_s": 20.0,
    "pass1_width": 640,
    "frame_cap": 240,
}
_ENV = {
    "scene_threshold": "YTIS_VISUAL_SCENE_THRESHOLD",
    "floor_interval_s": "YTIS_VISUAL_FLOOR_INTERVAL_S",
    "pass1_width": "YTIS_VISUAL_PASS1_WIDTH",
    "frame_cap": "YTIS_VISUAL_FRAME_CAP",
}
_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


class FrameExtractionError(NonFatalAnalysisError):
    """Frame extraction ran but failed; recoverable at the job level."""


@dataclass
class FrameSample:
    """Pass-1 extraction result."""

    frames: list[Path] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    duration_s: float = 0.0
    capped: bool = False
    timestamps_exact: bool = True


def _knob(name: str, value, *, cast=float):
    if value is not None:
        return value
    raw = os.environ.get(_ENV[name], "").strip()
    if not raw:
        return _DEFAULTS[name]
    try:
        return cast(raw)
    except ValueError:
        return _DEFAULTS[name]


def _ffmpeg_binary() -> str:
    override = os.environ.get("YTIS_VISUAL_FFMPEG", "").strip()
    if override:
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    # Agent shells often lack the WinGet links dir on PATH; ffmpeg lives in
    # the Gyan package (same resolution crv_run.py performs).
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        for candidate in Path(local).glob(
            "Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg*/bin/ffmpeg.exe"
        ):
            return str(candidate)
    raise RuntimeError(
        "ffmpeg not found on PATH or WinGet; set YTIS_VISUAL_FFMPEG or install ffmpeg"
    )


def probe_video(video_path: str | Path) -> dict:
    """Probe duration and frame rate. ffprobe preferred; ffmpeg -i fallback.

    A missing frame rate falls back to 30 fps — the periodic floor stays
    approximately correct and scene detection is fps-independent.
    """
    video_path = Path(video_path)
    duration_s = 0.0
    fps = 0.0
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            proc = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=r_frame_rate:format=duration",
                    "-of", "default=noprint_wrappers=1",
                    str(video_path),
                ],
                capture_output=True, text=True, timeout=60,
                creationflags=0x08000000,  # CREATE_NO_WINDOW: worker runs under consoleless pythonw
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if line.startswith("r_frame_rate="):
                        token = line.split("=", 1)[1].strip()
                        if "/" in token:
                            num, _, den = token.partition("/")
                            try:
                                if float(den):
                                    fps = float(num) / float(den)
                            except ValueError:
                                pass
                    elif line.startswith("duration="):
                        try:
                            duration_s = float(line.split("=", 1)[1])
                        except ValueError:
                            pass
        except (subprocess.TimeoutExpired, OSError):
            pass
    if duration_s <= 0 or fps <= 0:
        # ffmpeg -i fallback for duration (fps stays 0 -> caller default).
        try:
            proc = subprocess.run(
                [_ffmpeg_binary(), "-hide_banner", "-i", str(video_path)],
                capture_output=True, text=True, timeout=60,
                creationflags=0x08000000,  # CREATE_NO_WINDOW: worker runs under consoleless pythonw
            )
        except (subprocess.TimeoutExpired, OSError):
            proc = None
        if proc is not None:
            for line in (proc.stderr or "").splitlines():
                if "Duration:" in line:
                    token = line.split("Duration:", 1)[1].split(",")[0].strip()
                    try:
                        h, m, s = token.split(":")
                        duration_s = float(h) * 3600 + float(m) * 60 + float(s)
                    except (ValueError, IndexError):
                        pass
                    break
    return {"duration_s": duration_s, "fps": fps if fps > 0 else 30.0}


def extract_pass1(
    video_path: str | Path,
    out_dir: str | Path,
    *,
    scene_threshold: float | None = None,
    floor_interval_s: float | None = None,
    width: int | None = None,
    frame_cap: int | None = None,
) -> FrameSample:
    """Scene-change + periodic-floor frames at reduced width, with timestamps.

    Frames are written to ``<out_dir>/frames/frame_0001.jpg`` ... Timestamps
    come from ffmpeg showinfo on the selected output frames. When the sample
    exceeds ``frame_cap``, it is uniformly subsampled and the surplus frames
    are deleted from disk (pass 2 later adds native-res captures for the
    code-dense subset).
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    threshold = float(_knob("scene_threshold", scene_threshold))
    interval_s = float(_knob("floor_interval_s", floor_interval_s))
    pass1_width = int(_knob("pass1_width", width, cast=int))
    cap = int(_knob("frame_cap", frame_cap, cast=int))

    meta = probe_video(video_path)
    floor_n = max(1, round(meta["fps"] * max(interval_s, 0.1)))
    vf = (
        f"select='gt(scene,{threshold:g})+not(mod(n,{floor_n}))',"
        f"scale={pass1_width}:-2,showinfo"
    )
    cmd = [
        _ffmpeg_binary(), "-hide_banner", "-nostdin", "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-fps_mode", "vfr",
        "-q:v", "2",
        str(frames_dir / "frame_%04d.jpg"),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1200,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: worker runs under consoleless pythonw
        )
    except subprocess.TimeoutExpired as exc:
        raise FrameExtractionError(f"pass-1 ffmpeg timeout for {video_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"ffmpeg could not be executed: {exc}") from exc

    frames = sorted(frames_dir.glob("frame_*.jpg"))
    if proc.returncode != 0 and not frames:
        raise FrameExtractionError(
            f"pass-1 ffmpeg failed for {video_path} "
            f"(rc={proc.returncode}): {proc.stderr[-500:]}"
        )

    pts = [float(m.group(1)) for m in _PTS_RE.finditer(proc.stderr or "")]
    timestamps_exact = len(pts) == len(frames)
    if not timestamps_exact:
        # Honest degradation: keep parsed prefix, zero-fill the remainder.
        pts = (pts + [0.0] * len(frames))[: len(frames)]

    capped = len(frames) > cap
    if capped:
        step = len(frames) / cap
        keep = sorted({int(i * step) for i in range(cap)})
        keep_set = {frames[i] for i in keep}
        for frame in frames:
            if frame not in keep_set:
                frame.unlink(missing_ok=True)
        frames = [frames[i] for i in keep]
        pts = [pts[i] for i in keep]

    return FrameSample(
        frames=frames,
        timestamps=pts,
        duration_s=meta["duration_s"],
        capped=capped,
        timestamps_exact=timestamps_exact,
    )


def reextract_native(
    video_path: str | Path,
    timestamps: list[float],
    out_dir: str | Path,
) -> list[Path]:
    """Re-capture specific moments at native resolution (pass 2).

    One accurate seek per timestamp (``-ss`` before ``-i``). A failed seek
    skips that frame rather than failing the batch.
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    native_dir = out_dir / "native"
    native_dir.mkdir(parents=True, exist_ok=True)

    produced: list[Path] = []
    for index, ts in enumerate(timestamps):
        dest = native_dir / f"native_{index:04d}.jpg"
        cmd = [
            _ffmpeg_binary(), "-hide_banner", "-nostdin", "-y",
            "-ss", f"{max(ts, 0.0):.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(dest),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
                creationflags=0x08000000,  # CREATE_NO_WINDOW: worker runs under consoleless pythonw
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            produced.append(dest)
        else:
            dest.unlink(missing_ok=True)
    return produced


def select_code_dense_timestamps(
    sample: FrameSample,
    per_frame_text: list[str],
) -> list[float]:
    """Timestamps whose pass-1 OCR text looks code-dense.

    ``per_frame_text`` must align with ``sample.frames`` by index. Uses the
    shared code-density heuristics from csf.profiles.
    """
    from csf.profiles import is_code_dense

    timestamps: list[float] = []
    for index, text in enumerate(per_frame_text[: len(sample.frames)]):
        if text and is_code_dense(text):
            timestamps.append(sample.timestamps[index])
    return timestamps
