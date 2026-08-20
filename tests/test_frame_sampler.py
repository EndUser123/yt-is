"""Tests for the two-pass frame sampler (csf/visual/frame_sampler.py) and the
csf/video_utils.py temp-dir regression.

ffmpeg/ffprobe are mocked; these tests lock in command shape, timestamp
parsing honesty, cap subsampling with on-disk surplus deletion, native
re-extraction seeks, and the code-density timestamp selection used by the
worker's pass 2.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from csf.visual import frame_sampler as fs


class FakeProc(subprocess.CompletedProcess):
    def __init__(self, returncode=0, stdout="", stderr=""):
        super().__init__(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture()
def no_ffprobe(monkeypatch):
    monkeypatch.setattr(fs.shutil, "which", lambda name: None if name == "ffprobe" else "ffmpeg")


def _make_frames(frames_dir: Path, count: int) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        (frames_dir / f"frame_{i:04d}.jpg").write_bytes(b"x")


def _showinfo_stderr(count: int, start=0.0, step=5.0) -> str:
    lines = []
    for i in range(count):
        t = start + i * step
        lines.append(
            f"[Parsed_showinfo] n:{i} pts:{i*1000} pts_time:{t:.6f}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# pass 1
# ---------------------------------------------------------------------------


def test_pass1_extracts_frames_with_exact_timestamps(tmp_path, monkeypatch, no_ffprobe):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        _make_frames(Path(cmd[-1]).parent, 4)
        return FakeProc(0, stderr=_showinfo_stderr(4))

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    monkeypatch.setattr(fs, "probe_video", lambda v: {"duration_s": 600.0, "fps": 30.0})
    sample = fs.extract_pass1(tmp_path / "v.mp4", tmp_path / "out")
    assert len(sample.frames) == 4
    assert sample.timestamps == [0.0, 5.0, 10.0, 15.0]
    assert sample.timestamps_exact is True
    assert sample.capped is False
    cmd = " ".join(calls["cmd"])
    assert "select=" in cmd and "scale=640:-2" in cmd and "showinfo" in cmd
    # Periodic floor: 30 fps * 20s = every 600th frame.
    assert "mod(n,600)" in cmd


def test_pass1_cap_subsamples_and_deletes_surplus(tmp_path, monkeypatch, no_ffprobe):
    def fake_run(cmd, **kwargs):
        out = tmp_path / "out" / "frames"
        _make_frames(out, 10)
        return FakeProc(0, stderr=_showinfo_stderr(10))

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    monkeypatch.setattr(fs, "probe_video", lambda v: {"duration_s": 600.0, "fps": 30.0})
    sample = fs.extract_pass1(tmp_path / "v.mp4", tmp_path / "out", frame_cap=3)
    assert sample.capped is True
    assert len(sample.frames) == 3
    # Surplus frames deleted on disk; kept count matches returned count.
    on_disk = list((tmp_path / "out" / "frames").glob("frame_*.jpg"))
    assert len(on_disk) == 3
    # Timestamps align with the kept subset (monotonic).
    assert sample.timestamps == sorted(sample.timestamps)


def test_pass1_mismatched_showinfo_marks_timestamps_inexact(tmp_path, monkeypatch, no_ffprobe):
    def fake_run(cmd, **kwargs):
        _make_frames(tmp_path / "out" / "frames", 3)
        return FakeProc(0, stderr=_showinfo_stderr(2))  # one showinfo line lost

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    monkeypatch.setattr(fs, "probe_video", lambda v: {"duration_s": 60.0, "fps": 30.0})
    sample = fs.extract_pass1(tmp_path / "v.mp4", tmp_path / "out")
    assert sample.timestamps_exact is False
    assert sample.timestamps[:2] == [0.0, 5.0]
    assert sample.timestamps[2] == 0.0


def test_pass1_ffmpeg_failure_raises_recoverable(tmp_path, monkeypatch, no_ffprobe):
    monkeypatch.setattr(
        fs.subprocess, "run", lambda cmd, **kw: FakeProc(1, stderr="boom")
    )
    monkeypatch.setattr(fs, "probe_video", lambda v: {"duration_s": 60.0, "fps": 30.0})
    with pytest.raises(fs.FrameExtractionError):
        fs.extract_pass1(tmp_path / "v.mp4", tmp_path / "out")


# ---------------------------------------------------------------------------
# pass 2
# ---------------------------------------------------------------------------


def test_reextract_native_seek_commands_and_outputs(tmp_path, monkeypatch, no_ffprobe):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        dest = Path(cmd[-1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"n")
        return FakeProc(0)

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    produced = fs.reextract_native(tmp_path / "v.mp4", [12.5, 100.0], tmp_path / "out")
    assert len(produced) == 2
    assert produced[0].name == "native_0000.jpg"
    first = seen[0]
    assert first[first.index("-ss") + 1] == "12.500"
    assert "-frames:v" in first
    # No scale filter: native resolution.
    assert not any("scale" in part for part in first)


def test_reextract_native_skips_failed_seeks(tmp_path, monkeypatch, no_ffprobe):
    def fake_run(cmd, **kwargs):
        dest = Path(cmd[-1])
        if "native_0001" in dest.name:
            return FakeProc(1, stderr="seek failed")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"n")
        return FakeProc(0)

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    produced = fs.reextract_native(tmp_path / "v.mp4", [5.0, 10.0], tmp_path / "out")
    assert len(produced) == 1
    assert not (tmp_path / "out" / "native" / "native_0001.jpg").exists()


# ---------------------------------------------------------------------------
# code-density selection
# ---------------------------------------------------------------------------


def test_select_code_dense_timestamps_uses_profiles_heuristics(tmp_path):
    sample = fs.FrameSample(
        frames=[tmp_path / "a.jpg", tmp_path / "b.jpg", tmp_path / "c.jpg"],
        timestamps=[1.0, 2.0, 3.0],
    )
    per_frame = [
        "hello and welcome back to the channel",
        "def process(items):\n    return {i: i for i in items}",
        "plain sentence without markers",
    ]
    assert fs.select_code_dense_timestamps(sample, per_frame) == [2.0]


def test_profiles_is_code_dense():
    from csf.profiles import is_code_dense

    assert is_code_dense("def main():\n    return 0") is True
    assert is_code_dense("") is False
    assert is_code_dense("just a normal sentence about videos") is False


# ---------------------------------------------------------------------------
# video_utils regression: frames must survive the call
# ---------------------------------------------------------------------------


def test_video_utils_frames_survive_and_out_dir_respected(tmp_path, monkeypatch):
    from csf import video_utils as vu

    def fake_run(cmd, **kwargs):
        pattern = cmd[-1]
        out_dir = Path(pattern).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 4):
            (out_dir / f"frame_{i:03d}.jpg").write_bytes(b"f")
        return FakeProc(0)

    monkeypatch.setattr(vu.subprocess, "run", fake_run)
    monkeypatch.setattr(
        vu, "_parse_duration_ffmpeg", lambda p: 10.0
    )
    frames = vu.extract_frames(tmp_path / "v.mp4", fps=1.0, max_frames=5)
    assert frames, "extract_frames returned no frames"
    assert all(f.exists() for f in frames), "returned frame paths were deleted"

    persistent = tmp_path / "keep"
    frames2 = vu.extract_frames(
        tmp_path / "v.mp4", fps=1.0, max_frames=5, out_dir=persistent
    )
    assert persistent.is_dir()
    assert all(f.exists() for f in frames2)
    assert all(persistent in f.parents for f in frames2)
