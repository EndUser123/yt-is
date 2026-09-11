"""Tests for the deferred-audio backlog feeder.

Real fixture stores/files end to end: real sqlite DBs, real audio-ish
files, real subprocess worker (a fast deterministic double at the process
boundary — faster-whisper itself is the external dependency), real cache
writes into a real transcript DB, real ledgered unlinks. The deletion
rule is exercised both ways: cached items evict; transcript-less items
never unlink.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.deferred_audio_feeder import (  # noqa: E402
    check_model_cache,
    classify_audio_row,
    evictable,
    pick_distribution,
)
from scripts.audio_drain_relay import parse_done_line  # noqa: E402


# ------------------------------------------------------------------ pure


def test_classify_buckets_by_floor_and_status():
    floor_big = 1_000_000
    assert classify_audio_row("complete", floor_big) == "complete"
    assert classify_audio_row("deferred_audio", floor_big) == "backlog"
    assert classify_audio_row(None, floor_big) == "backlog"
    assert classify_audio_row("failed", 100) == "subfloor"
    assert classify_audio_row("complete", 0) == "subfloor"


def test_pick_distribution_spans_smallest_to_largest():
    items = [
        {"video_id": f"v{i:02d}", "bytes": 1_000_000 * (i + 1)}
        for i in range(10)
    ]
    picked = pick_distribution(items, 5)
    assert [p["bytes"] for p in picked] == [
        1_000_000, 3_000_000, 5_000_000, 7_000_000, 10_000_000,
    ]
    assert picked[0]["bytes"] == min(i["bytes"] for i in items)
    assert picked[-1]["bytes"] == max(i["bytes"] for i in items)


def test_pick_distribution_edge_cases():
    items = [{"video_id": "v1", "bytes": 5}]
    assert pick_distribution(items, 0) == []
    assert pick_distribution([], 3) == []
    assert pick_distribution(items, 5) == items


def test_evict_decision_follows_deletion_rule():
    assert evictable(True) is True
    assert evictable(False) is False


def test_model_cache_preflight_reports_missing_and_partial(tmp_path):
    home = tmp_path / "mc"
    verdict = check_model_cache(home)
    assert verdict["ok"] is False
    assert "missing" in verdict["error"]
    blob = home / "hub" / "models--x" / "snapshots" / "s1" / "model.bin"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"0" * 100)
    verdict = check_model_cache(home)
    assert verdict["ok"] is False
    assert "partial" in verdict["error"]
    blob.write_bytes(b"0" * 1_600_000_000)
    verdict = check_model_cache(home)
    assert verdict["ok"] is True
    assert verdict["bytes"] == 1_600_000_000


def test_reconcile_drops_stale_cached_claims(feeder_env):
    audio = feeder_env.add_video("v_r12345678", "deferred_audio", 250_000)
    assert _run_cli("inventory", "--manifest", str(feeder_env.media_root / "m.json")).returncode == 0
    assert _run_cli("process", "--limit", "5",
                    "--manifest", str(feeder_env.media_root / "m.json")).returncode == 0
    checkpoint = json.loads(feeder_env.checkpoint.read_text(encoding="utf-8"))
    assert checkpoint["v_r12345678"]["state"] == "cached"

    # Destroy the transcript row: reconcile must drop the stale claim.
    con = sqlite3.connect(feeder_env.cache_db)
    con.execute("DELETE FROM transcript_cache WHERE video_id = 'v_r12345678'")
    con.commit()
    con.close()
    rec = _run_cli("reconcile")
    assert rec.returncode == 0, rec.stderr
    checkpoint = json.loads(feeder_env.checkpoint.read_text(encoding="utf-8"))
    assert "v_r12345678" not in checkpoint
    assert "dropped 1 stale-cached" in rec.stdout

    # And the item retries instead of being stranded.
    proc = _run_cli("process", "--limit", "5",
                    "--manifest", str(feeder_env.media_root / "m.json"))
    assert proc.returncode == 0, proc.stderr
    checkpoint = json.loads(feeder_env.checkpoint.read_text(encoding="utf-8"))
    assert checkpoint["v_r12345678"]["state"] == "cached"
    assert audio.exists()


def test_parse_done_line_counts():
    counts = parse_done_line("backlog 10, checkpointed 0, processing 5 (CPU)\ndone: cached=3 refused=1 errors=1")
    assert counts == {"cached": 3, "refused": 1, "errors": 1}
    assert parse_done_line("no summary here") == {"cached": 0, "refused": 0, "errors": 0}


def test_drain_command_end_to_end(feeder_env):
    audio_a = feeder_env.add_video("v_d12345678", "deferred_audio", 300_000)
    audio_b = feeder_env.add_video("v_e12345678", "failed", 200_000)
    manifest = feeder_env.media_root / "m.json"
    checkpoint = feeder_env.media_root / "cp.json"
    assert _run_cli("inventory", "--manifest", str(manifest)).returncode == 0
    drain = _run_cli("drain", "--limit", "5", "--manifest", str(manifest),
                     "--checkpoint", str(checkpoint))
    assert drain.returncode == 0, drain.stderr
    assert not audio_a.exists()
    assert not audio_b.exists()
    ledger = feeder_env.media_root / "deletion-ledger.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines()]
    assert {r["video_id"] for r in rows} == {"v_d12345678", "v_e12345678"}
    assert sum(r["bytes"] for r in rows) == 500_000


def test_process_refuses_without_model_cache(feeder_env, monkeypatch):
    monkeypatch.setenv("YTIS_FEEDER_MODEL_CACHE", str(feeder_env.media_root / "no-such-cache"))
    feeder_env.add_video("v_m12345678", "deferred_audio", 250_000)
    manifest = feeder_env.media_root / "m.json"
    assert _run_cli("inventory", "--manifest", str(manifest)).returncode == 0
    proc = _run_cli("process", "--limit", "5", "--manifest", str(manifest))
    assert proc.returncode == 4
    assert "model cache" in proc.stdout.lower() or "model.bin" in proc.stdout


# ------------------------------------------------------- fixture harness


WORKER_STUB = """
import json
import sys
from pathlib import Path

args = sys.argv[1:]
opts = dict(zip(args[::2], args[1::2]))
result = {"ok": True, "transcript": "stub transcript text for " + Path(opts["--audio-file"]).stem}
Path(opts["--result-path"]).write_text(json.dumps(result), encoding="utf-8")
"""


@pytest.fixture()
def feeder_env(tmp_path, monkeypatch):
    media = tmp_path / "visual"
    media.mkdir()
    batch = tmp_path / "batch.sqlite"
    cache = tmp_path / "cache.sqlite"
    checkpoint = tmp_path / "checkpoint.json"

    con = sqlite3.connect(batch)
    con.executescript(
        """
        CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE transcript_status (video_id TEXT PRIMARY KEY, status TEXT);
        """
    )
    con.commit()
    con.close()

    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    (stub_dir / "stub_worker.py").write_text(WORKER_STUB, encoding="utf-8")

    monkeypatch.setenv("YTIS_VISUAL_MEDIA_ROOT", str(media))
    monkeypatch.setenv("YTIS_BATCH_DB", str(batch))
    monkeypatch.setenv("YTIS_TRANSCRIPT_CACHE_DB_PATH", str(cache))
    monkeypatch.setenv("YTIS_FEEDER_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv("YTIS_FEEDER_WORKER", "stub_worker")
    monkeypatch.setenv("PYTHONPATH", str(stub_dir))
    monkeypatch.setenv("YTIS_FEEDER_TIMEOUT_S", "120")
    monkeypatch.setenv("YTIS_FEEDER_MIN_FREE_BYTES", "0")

    def add_video(video_id: str, status: str, audio_bytes: int) -> Path:
        vdir = media / video_id
        vdir.mkdir()
        audio = vdir / "audio.mka"
        audio.write_bytes(b"a" * audio_bytes)
        con = sqlite3.connect(batch)
        con.execute(
            "INSERT OR REPLACE INTO analysis_status VALUES (?, ?)",
            (video_id, status),
        )
        con.commit()
        con.close()
        return audio

    harness = type(
        "Harness", (), {
            "media_root": media, "batch_db": batch, "cache_db": cache,
            "checkpoint": checkpoint, "add_video": staticmethod(add_video),
        },
    )()
    return harness


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.deferred_audio_feeder", *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )


def test_inventory_builds_manifest_from_stores(feeder_env):
    feeder_env.add_video("v_def1234567", "deferred_audio", 500_000)
    feeder_env.add_video("v_done123456", "complete", 400_000)
    feeder_env.add_video("v_tiny123456", "deferred_audio", 100)
    result = _run_cli("inventory", "--manifest", str(feeder_env.media_root / "m.json"))
    assert result.returncode == 0, result.stderr
    manifest = json.loads(
        (feeder_env.media_root / "m.json").read_text(encoding="utf-8")
    )
    by_vid = {i["video_id"]: i for i in manifest["items"]}
    assert by_vid["v_def1234567"]["bucket"] == "backlog"
    assert by_vid["v_def1234567"]["status"] == "deferred_audio"
    assert by_vid["v_done123456"]["bucket"] == "complete"
    assert by_vid["v_tiny123456"]["bucket"] == "subfloor"
    assert manifest["totals"]["backlog"]["files"] == 1
    assert manifest["totals"]["backlog"]["bytes"] == 500_000


def test_process_then_evict_full_lifecycle(feeder_env):
    manifest_path = feeder_env.media_root / "m.json"
    audio_def = feeder_env.add_video("v_a12345678", "deferred_audio", 300_000)
    audio_def2 = feeder_env.add_video("v_b12345678", "failed", 200_000)
    feeder_env.add_video("v_c12345678", "deferred_audio", 50)  # subfloor, never processed

    run1 = _run_cli("inventory", "--manifest", str(manifest_path))
    assert run1.returncode == 0, run1.stderr
    proc = _run_cli("process", "--limit", "5", "--manifest", str(manifest_path))
    assert proc.returncode == 0, proc.stderr
    checkpoint = json.loads(feeder_env.checkpoint.read_text(encoding="utf-8"))
    assert checkpoint["v_a12345678"]["state"] == "cached"
    assert checkpoint["v_b12345678"]["state"] == "cached"

    # resumability: a second identical run skips the checkpointed items
    proc2 = _run_cli("process", "--limit", "5", "--manifest", str(manifest_path))
    assert proc2.returncode == 0, proc2.stderr
    assert "processing 0" in proc2.stdout

    dry = _run_cli("evict", "--dry-run", "--manifest", str(manifest_path),
                   "--out", str(feeder_env.media_root / "dry.json"))
    assert dry.returncode == 0, dry.stderr
    dry_manifest = json.loads(
        (feeder_env.media_root / "dry.json").read_text(encoding="utf-8")
    )
    assert dry_manifest["unlinks"] == 0
    assert dry_manifest["files"] == 2
    assert {i["video_id"] for i in dry_manifest["items"]} == {
        "v_a12345678", "v_b12345678",
    }
    assert audio_def.exists() and audio_def2.exists()  # zero unlinks in dry run

    apply_run = _run_cli("evict", "--apply", "--manifest", str(manifest_path))
    assert apply_run.returncode == 0, apply_run.stderr
    assert not audio_def.exists()
    assert not audio_def2.exists()

    ledger = feeder_env.media_root / "deletion-ledger.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert {r["video_id"] for r in rows} == {"v_a12345678", "v_b12345678"}
    assert all(r["reason"] == "transcript_complete" for r in rows)
    assert sum(r["bytes"] for r in rows) == 500_000


def test_transcriptless_item_is_never_unlinked(feeder_env):
    manifest_path = feeder_env.media_root / "m.json"
    audio = feeder_env.add_video("v_x12345678", "deferred_audio", 250_000)
    assert _run_cli("inventory", "--manifest", str(manifest_path)).returncode == 0

    # Process, then destroy the cached transcript row: the item's
    # transcript row no longer exists, so the deletion rule forbids
    # unlinking even though the checkpoint says "cached".
    assert _run_cli(
        "process", "--limit", "5", "--manifest", str(manifest_path)
    ).returncode == 0
    con = sqlite3.connect(feeder_env.cache_db)
    con.execute("DELETE FROM transcript_cache WHERE video_id = 'v_x12345678'")
    con.commit()
    con.close()

    dry = _run_cli("evict", "--dry-run", "--manifest", str(manifest_path),
                   "--out", str(feeder_env.media_root / "dry.json"))
    assert dry.returncode == 0, dry.stderr
    dry_manifest = json.loads(
        (feeder_env.media_root / "dry.json").read_text(encoding="utf-8")
    )
    assert dry_manifest["files"] == 0

    apply_run = _run_cli("evict", "--apply", "--manifest", str(manifest_path))
    assert apply_run.returncode == 0, apply_run.stderr
    assert audio.exists(), "transcript-less audio must never be unlinked"
