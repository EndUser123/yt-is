from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

import pytest

import scripts.apply_channel_review as apply_mod
import scripts.backfill_channel_descriptions as backfill_mod
import scripts.build_channel_review_page as build_mod
import scripts.promote_excluded_categories as promote_mod
from csf.batch_status import (
    _BatchStatusStorage,
    is_channel_blocked,
    upsert_channel,
)


def _load_csf_source():
    repo_root = Path(__file__).resolve().parents[1]
    loader = SourceFileLoader("csf_source_intake_workflow_test", str(repo_root / "bin" / "csf-source"))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHANNELS = [
    # url-suffix, channel_id, title, tier1_description (None = tier-1 fails)
    ("UCaaa", "UCaaa", "Alpha ML Lab", "machine learning research explainers"),
    ("UCbbb", "UCbbb", "Beta News Network", "breaking world news coverage"),
    ("UCCCC", "UCCCC", "Gamma Personal", None),  # tier-1 fails, API supplies it
]


def _seed(tmp_path: Path) -> Path:
    db = tmp_path / "batch.sqlite"
    storage = _BatchStatusStorage(db_path=db)
    conn = storage._get_conn()
    conn.executemany(
        "INSERT INTO channel_metadata (channel_url, channel_id, last_checked,"
        " channel_title, description, category) VALUES (?, ?, ?, ?, ?, NULL)",
        [(f"https://www.youtube.com/channel/{cid}", cid, "2026-08-15", title, "") for _, cid, title, _ in CHANNELS],
    )
    conn.commit()
    conn.close()
    return db


def _description_in_db(db: Path, url: str) -> str:
    conn = sqlite3.connect(db)
    value = conn.execute(
        "SELECT COALESCE(description,'') FROM channel_metadata WHERE channel_url=?", (url,)
    ).fetchone()[0]
    conn.close()
    return value


def test_backfill_tier1_then_api_fallback(tmp_path, monkeypatch):
    db = _seed(tmp_path)

    def fake_ytdlp(url: str):
        for _, cid, title, desc in CHANNELS:
            channel_url = f"https://www.youtube.com/channel/{cid}"
            if url.startswith(channel_url) and desc is not None:
                fields = {"description": desc}
                if title:
                    fields["channel_title"] = title
                return fields
        return None

    def fake_api(key, ids):
        # API returns the tier-1 failure with title, description + subscriber count
        return [
            {"id": "UCCCC", "snippet": {"description": "piano performances", "title": "Gamma Personal"},
             "statistics": {"subscriberCount": "4242"}}
        ]

    monkeypatch.setattr(backfill_mod, "_fetch_via_ytdlp", fake_ytdlp)
    monkeypatch.setattr(backfill_mod, "_channels_list", fake_api)
    monkeypatch.setattr(backfill_mod, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    receipt = backfill_mod.backfill(db, allow_spend=True, ytdlp_pace_s=0)
    assert receipt["updated_via_ytdlp"] == 2
    assert receipt["updated_via_api"] == 1
    assert receipt["unresolved"] == 0
    assert _description_in_db(db, "https://www.youtube.com/channel/UCaaa") == "machine learning research explainers"
    assert _description_in_db(db, "https://www.youtube.com/channel/UCCCC") == "piano performances"
    conn = sqlite3.connect(db)
    subs, title = conn.execute(
        "SELECT subscriber_count, channel_title FROM channel_metadata WHERE channel_id='UCCCC'"
    ).fetchone()
    conn.close()
    assert subs == 4242
    assert title == "Gamma Personal"  # API tier also backfills the missing title


def test_backfill_without_spend_authorization_skips_api(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    monkeypatch.setattr(backfill_mod, "_fetch_via_ytdlp", lambda url: None)
    monkeypatch.setattr(backfill_mod, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    receipt = backfill_mod.backfill(db, allow_spend=False, ytdlp_pace_s=0)
    assert receipt["updated_via_ytdlp"] == 0
    assert receipt["updated_via_api"] == 0  # API tier never runs
    assert receipt["api_used"] is False


def test_channel_intake_workflow_end_to_end(tmp_path, monkeypatch):
    """The operator journey: import → describe → classify → review → apply → promote."""
    db = _seed(tmp_path)

    # Step 1: descriptions (tier-1 only, no API).
    def fake_ytdlp(url: str):
        for _, cid, title, desc in CHANNELS:
            if url.startswith(f"https://www.youtube.com/channel/{cid}") and desc:
                return {"description": desc, "channel_title": title}
        return None

    monkeypatch.setattr(backfill_mod, "_fetch_via_ytdlp", fake_ytdlp)
    monkeypatch.setattr(backfill_mod, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    backfill_receipt = backfill_mod.backfill(db, allow_spend=False, ytdlp_pace_s=0)
    assert backfill_receipt["updated_via_ytdlp"] == 2

    # Step 2: classification (provider chain mocked to a title-based rule).
    import csf.categorize as categorize_mod
    import csf.batch_status as batch_status_mod

    real_storage = _BatchStatusStorage(db_path=db)
    monkeypatch.setattr(batch_status_mod, "_get_batch_status_storage", lambda: real_storage)

    def fake_classify(title: str, desc: str, video_titles=None):
        t = (title or "").lower()
        if "ml" in t or "machine" in t:
            return "AI/ML"
        if "news" in t:
            return "News"
        return None  # Gamma stays unclassified -> review page handles it

    monkeypatch.setattr(categorize_mod, "categorize_channel", fake_classify)
    csf_source = _load_csf_source()
    csf_source.cmd_categorize()  # exits clean on partial success

    conn = sqlite3.connect(db)
    cats = dict(conn.execute("SELECT channel_id, category FROM channel_metadata"))
    sources = dict(conn.execute("SELECT channel_id, category_source FROM channel_metadata"))
    conn.close()
    assert cats["UCaaa"] == "AI/ML" and cats["UCbbb"] == "News"
    assert sources["UCaaa"] == "llm"  # auto-classified

    # Step 3: review page over the classified + one unclassified channel.
    out = tmp_path / "review.html"
    stats = build_mod.build_page(db, out, excluded=["News"])
    assert stats["channels"] == 3
    assert "review.html" in str(out) and out.stat().st_size > 1000

    # Step 4: operator decisions from the page.
    def fake_promotion_cmd(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = json.dumps({"mode": "dry-run", "candidates": 1, "promoted": 0})
            stderr = ""
        return R()

    monkeypatch.setattr(apply_mod.subprocess, "run", fake_promotion_cmd)
    settings = tmp_path / "discovery-settings.json"
    settings.write_text(json.dumps({"excluded_categories": [], "cookies_browser": "x"}), encoding="utf-8")
    decisions = {
        "assignments": {"https://www.youtube.com/channel/UCCCC": "Music"},
        "excluded_categories": ["News"],
        "block_urls": ["https://www.youtube.com/channel/UCaaa"],
    }
    apply_receipt = apply_mod.apply_decisions(
        decisions, db_path=db, settings_path=settings, apply_promotion=False
    )
    assert apply_receipt["assignments_applied"] == 1
    assert apply_receipt["channels_blocked"] == 1
    assert apply_receipt["settings_updated"] is True
    assert json.loads(settings.read_text(encoding="utf-8"))["excluded_categories"] == ["News"]

    # Step 5: real promotion on the tmp DB (no mocking — the blocklist is the
    # contract every enforcement point consumes).
    promote_receipt = promote_mod.promote(db_path=db, excluded_categories=frozenset({"News"}), apply=True)
    assert promote_receipt["promoted"] == 1

    # Final state: every channel classified, exclusions enforced.
    conn = sqlite3.connect(db)
    final = dict(conn.execute("SELECT channel_id, category FROM channel_metadata"))
    conn.close()
    assert final == {"UCaaa": "AI/ML", "UCbbb": "News", "UCCCC": "Music"}
    assert is_channel_blocked("https://www.youtube.com/channel/UCbbb", db_path=db) is True  # excluded category
    assert is_channel_blocked("https://www.youtube.com/channel/UCaaa", db_path=db) is True  # per-channel block
    assert is_channel_blocked("https://www.youtube.com/channel/UCCCC", db_path=db) is False

    # Retry-other (run last — it adds a channel): stored Others re-queue for
    # re-classification, but MANUAL decisions never reset. Gamma (still NULL,
    # unclassifiable by the fake) makes this a zero-success pass, so the
    # loud-failure contract fires — the invariant is Delta's survival.
    upsert_channel(
        "https://www.youtube.com/channel/UCddd", db_path=db, channel_id="UCddd",
        last_checked="2026-08-15", channel_title="Delta Other", description="x",
    )
    upsert_channel(
        "https://www.youtube.com/channel/UCddd", db_path=db,
        category="Other", category_source="manual",
    )
    upsert_channel(
        "https://www.youtube.com/channel/UCeee", db_path=db, channel_id="UCeee",
        last_checked="2026-08-15", channel_title="Echo Other", description="x",
        category="Other",  # auto/legacy Other: retry must re-queue this one
    )
    with pytest.raises(SystemExit):
        # Echo re-queues and stays unclassifiable (fake returns None) -> zero
        # success with candidates -> the loud-failure contract fires.
        csf_source.cmd_categorize(retry_other=True)
    conn = sqlite3.connect(db)
    delta, echo = conn.execute(
        "SELECT category, category_source FROM channel_metadata WHERE channel_id='UCddd'"
    ).fetchone(), conn.execute(
        "SELECT category FROM channel_metadata WHERE channel_id='UCeee'"
    ).fetchone()
    conn.close()
    assert delta == ("Other", "manual")  # manual Other survives retry
    assert echo == (None,)               # legacy Other was re-queued (reset to NULL)


def test_tab_count_absent_tab_is_zero_not_failure(monkeypatch):
    import scripts.backfill_channel_stats as stats_mod

    class _FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            if url.endswith("/shorts"):
                raise RuntimeError("This channel does not have a shorts tab")
            return {"entries": [{"id": f"p{i}"} for i in range(4)]}

    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)
    assert stats_mod._fetch_tab_count("https://www.youtube.com/channel/UCx", "shorts") == 0
    assert stats_mod._fetch_tab_count("https://www.youtube.com/channel/UCx", "playlists") == 4


def test_check_shorts_parses_tab_and_absent_tab(monkeypatch):
    from csf import source_enumerator as se

    class _FakeYDL:
        mode = "present"

        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            if _FakeYDL.mode == "absent":
                raise RuntimeError("This channel does not have a shorts tab")
            return {"entries": [{"id": "s1", "title": "Short One"}, {"id": "s2"}, None]}

    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)
    result = se.check_shorts("UCx")
    assert result == [{"video_id": "s1", "title": "Short One"}, {"video_id": "s2", "title": ""}]
    _FakeYDL.mode = "absent"
    assert se.check_shorts("UCx") == []  # absent tab is not an error


def test_spend_budget_blocks_when_exhausted():
    from csf import source_enumerator as se

    se.set_spend_authorized(True)
    try:
        se.set_spend_budget(2)
        assert se.spend_used() == 0
        # Simulate crossing the ceiling without HTTP: the gate lives in
        # _api_request before any network, so call with an unreachable key
        # shape via the budget check directly.
        se._spend_used = 2  # pretend two units consumed
        import pytest as _pytest

        with _pytest.raises(se.QuotaBudgetBlocked, match="budget exhausted"):
            se._api_request("videos", {"key": "k"}, unit_cost=1)
        assert se.spend_used() == 2  # counter preserved (before clearing)
        se.set_spend_budget(None)  # unlimited again (also resets the counter)
    finally:
        se.set_spend_authorized(False)
        se.set_spend_budget(None)
        se._spend_used = 0


def test_categorize_all_overrides_manual_marks(tmp_path, monkeypatch):
    """The mistake-fixing hammer: --all reclassifies even sticky manual rows."""
    import csf.batch_status as bs
    import csf.categorize as categorize_mod
    from csf.batch_status import upsert_channel

    db = tmp_path / "batch.sqlite"
    _BatchStatusStorage(db_path=db)
    upsert_channel("https://www.youtube.com/channel/UCman1", db_path=db,
                   channel_id="UCman1", channel_title="Manual Channel",
                   description="x", category="News", category_source="manual")
    upsert_channel("https://www.youtube.com/channel/UCllm1", db_path=db,
                   channel_id="UCllm1", channel_title="LLM Channel",
                   description="y", category="News", category_source="llm")

    storage = _BatchStatusStorage(db_path=db)
    monkeypatch.setattr(bs, "_get_batch_status_storage", lambda: storage)
    monkeypatch.setattr(categorize_mod, "categorize_channel",
                        lambda t, d, video_titles=None: "Science")
    csf_source = _load_csf_source()
    csf_source.cmd_categorize(all_channels=True)

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT channel_id, category FROM channel_metadata"))
    sources = dict(conn.execute("SELECT channel_id, category_source FROM channel_metadata"))
    conn.close()
    # Both re-derived — the manual mark did NOT protect the row from --all.
    assert rows == {"UCman1": "Science", "UCllm1": "Science"}
    assert sources == {"UCman1": "llm", "UCllm1": "llm"}


def test_blacklist_deletes_and_tombstones(tmp_path):
    import scripts.blacklist_channels as bl

    db = tmp_path / "batch.sqlite"
    _BatchStatusStorage(db_path=db)
    upsert_channel("https://www.youtube.com/channel/UCspam", db_path=db,
                   channel_id="UCspam", channel_title="Spam Channel",
                   channel_status="terminated")
    from csf.batch_status import set_status_batch, BatchEntry

    set_status_batch([BatchEntry(video_id="vidS", status="pending",
                                 source="https://www.youtube.com/channel/UCspam")],
                     db_path=db)
    tdb = tmp_path / "transcripts.sqlite"
    import sqlite3 as sq

    tc = sq.connect(tdb)
    tc.execute("CREATE TABLE transcripts (video_id TEXT PRIMARY KEY, content TEXT)")
    tc.execute("INSERT INTO transcripts VALUES ('vidS', 'x')")
    tc.commit(); tc.close()

    # Dry run: nothing deleted
    receipt = bl._plan(db, {"https://www.youtube.com/channel/UCspam"})
    assert receipt["channels"] == 1 and receipt["analysis_rows"] == 1

    result = bl._apply(db, tdb, {"https://www.youtube.com/channel/UCspam"}, "test")
    assert result["deleted_channels"] == 1
    assert result["deleted_analysis_rows"] == 1
    assert result["deleted_transcript_rows"] == 1

    conn = sq.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM channel_metadata").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM analysis_status").fetchone()[0] == 0
    # Tombstone: blocklist row + reason survive the metadata deletion
    assert conn.execute("SELECT COUNT(*) FROM channel_blocklist WHERE channel_url=?",
                        ("https://www.youtube.com/channel/UCspam",)).fetchone()[0] == 1
    assert conn.execute("SELECT reason FROM channel_blacklist_reason").fetchone()[0] == "test"
    conn.close()
    tc = sq.connect(tdb)
    assert tc.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 0
    tc.close()


def test_pipeline_verify_blocked_pending_is_warning():
    """Blocked-channel pending rows must not block the pipeline (coordinator skips them)."""
    import scripts.run_intake_pipeline as pl

    # Simulate a DB with blocked pending
    import tempfile
    from csf.batch_status import block_channel, set_status_batch, BatchEntry

    tmp = Path(tempfile.mkdtemp())
    db = tmp / "batch.sqlite"
    _BatchStatusStorage(db_path=db)
    upsert_channel("https://www.youtube.com/channel/UCblk", db_path=db,
                   channel_id="UCblk", channel_title="Blocked Channel",
                   channel_status="terminated")
    set_status_batch(
        [BatchEntry(video_id="vidB", status="pending",
                    source="https://www.youtube.com/channel/UCblk")],
        db_path=db,
    )
    block_channel("https://www.youtube.com/channel/UCblk", db_path=db, reason="dead")

    result = pl.phase_verify(db, pre_pending=0)
    assert result["ok"] is True  # warnings don't block
    assert len(result["warnings"]) == 1
    assert "blocked channels" in result["warnings"][0]


def test_pipeline_fetch_forwards_max_chunks(tmp_path, monkeypatch):
    """phase_fetch must pass --max-chunks so the supervisor accepts the resume.

    The supervisor compares max_chunks against the paused state's recorded
    config and rejects any drift, so omitting the flag (supervisor default 1)
    would abort a 50-chunk campaign resume with a config error.
    """
    import scripts.run_intake_pipeline as pl

    captured = {}

    class _FakeResult:
        returncode = 0
        stdout = '{"status": "paused"}'
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeResult()

    monkeypatch.setattr(pl.subprocess, "run", _fake_run)
    receipt = pl.phase_fetch(
        Path("batch.sqlite"), tmp_path, Path("state.json"),
        chunk_size=400, workers=3, batch_size=50,
        execute=True, max_chunks=50,
    )
    cmd = captured["cmd"]
    assert "--execute" in cmd
    assert "--max-chunks" in cmd
    assert cmd[cmd.index("--max-chunks") + 1] == "50"
    assert receipt["mode"] == "EXECUTE"


def test_verify_transcript_storage_clean(tmp_path):
    """The verification script must detect orphans and empty transcripts."""
    import scripts.verify_transcript_storage as vt

    bdb_path = tmp_path / "batch.sqlite"
    tdb_path = tmp_path / "transcripts.sqlite"

    # Build minimal batch DB
    _BatchStatusStorage(db_path=bdb_path)
    from csf.batch_status import set_status_batch, BatchEntry
    set_status_batch([
        BatchEntry(video_id="vidOK", status="complete", source="https://x"),
        BatchEntry(video_id="vidORPHAN", status="complete", source="https://x"),
        BatchEntry(video_id="vidPENDING", status="pending", source="https://x"),
    ], db_path=bdb_path)

    # Build minimal transcript DB
    import sqlite3
    tdb = sqlite3.connect(tdb_path)
    tdb.execute("""CREATE TABLE transcript_cache (
        cache_key TEXT PRIMARY KEY, video_id TEXT, lang TEXT, source TEXT,
        transcript TEXT, metadata_json TEXT, cached_at TEXT, terminal_id TEXT
    )""")
    tdb.execute("INSERT INTO transcript_cache VALUES ('k1','vidOK','en','test','hello world '*10,'{}','2026-01-01','t1')")
    tdb.execute("INSERT INTO transcript_cache VALUES ('k2','vidEMPTY','en','test','','{}','2026-01-01','t2')")
    tdb.execute("INSERT INTO transcript_cache VALUES ('k3','vidSHORT','en','test','ab','{}','2026-01-01','t3')")
    tdb.execute("INSERT INTO transcript_cache VALUES ('k4','vidPENDING','en','test','pending transcript '*5,'{}','2026-01-01','t4')")
    tdb.commit()
    tdb.close()

    receipt = vt.verify(bdb_path, tdb_path, suspect_min=50)
    # vidORPHAN is complete without cache → orphan detected
    assert receipt["orphans_complete_without_cache"] == 1
    # vidEMPTY has empty content → detected
    assert receipt["empty_or_null"] == 1
    # vidSHORT is 2 chars → suspect detected
    assert receipt["suspect_short"] >= 1
    # vidPENDING has cache but isn't complete → unclaimed
    assert receipt["unclaimed_cache_on_incomplete"] >= 1
    # Not clean (issues found)
    assert receipt["clean"] is False
    assert any("orphan" in i.lower() for i in receipt["issues"])
