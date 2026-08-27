"""Discriminating synthetic tests for the temporal time-policy lane
(packet-required cases 1-7; no production stores touched)."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
_scripts = str(REPO / "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import temporal_time_policy as ttp  # noqa: E402

import pytest  # noqa: E402


# --------------------------------------------------------------------------
# fixture machinery
# --------------------------------------------------------------------------

EU_BASE = {"media_kind": "transcript", "video_id": "", "channel_id": "",
           "title": "", "lang": "en", "source": "notebooklm",
           "content_hash": "", "published_at": "", "captured_at": "",
           "authority_ref": ""}


def _eu(eid, pub, cap, ch="cA", src="notebooklm", aref=None):
    d = dict(EU_BASE)
    d.update({"eu_id": eid, "video_id": eid, "published_at": pub,
              "captured_at": cap, "channel_id": ch, "source": src,
              "authority_ref": aref or ("auth:" + eid)})
    return d


def _mk_snapshot(tmp_path: Path, eus: list[dict],
                 mentions: list[tuple[str, str]]) -> Path:
    snap = tmp_path / "snapshot.sqlite"
    con = sqlite3.connect(str(snap))
    con.execute(
        """CREATE TABLE eu (eu_id TEXT PRIMARY KEY, media_kind TEXT,
           video_id TEXT, channel_id TEXT, channel_title TEXT DEFAULT '',
           title TEXT DEFAULT '', lang TEXT DEFAULT 'en', source TEXT,
           authority_ref TEXT, content_hash TEXT DEFAULT '',
           captured_at TEXT, published_at TEXT)""")
    con.execute("CREATE TABLE kg_nodes (node_id TEXT PRIMARY KEY,"
                " label TEXT, kind TEXT)")
    con.execute("CREATE TABLE kg_edges (src_id TEXT, dst_id TEXT,"
                " relation TEXT)")
    for u in eus:
        con.execute(
            "INSERT INTO eu (eu_id, media_kind, video_id, channel_id,"
            " source, authority_ref, captured_at, published_at)"
            " VALUES (:eu_id,:media_kind,:video_id,:channel_id,:source,"
            ":authority_ref,:captured_at,:published_at)", u)
        con.execute("INSERT OR IGNORE INTO kg_nodes VALUES ('ch_'||:channel_id,"
                    "'ch-'||:channel_id,'channel')", u)
    nodes = sorted({m[0] for m in mentions})
    for n in nodes:
        con.execute("INSERT INTO kg_nodes VALUES (?,'X','entity')", (n,))
    for nid, eid in mentions:
        con.execute("INSERT INTO kg_edges VALUES (?,?,?)",
                    (nid, "eu:" + eid, "mentioned_in"))
    con.commit()
    con.close()
    return snap


def _mk_transcripts_db(tmp_path: Path, cache_rows: list[tuple]) -> Path:
    tdb = tmp_path / "t.sqlite"
    con = sqlite3.connect(str(tdb))
    con.execute("CREATE TABLE transcript_cache (cache_key TEXT PRIMARY KEY,"
                " metadata_json TEXT, cached_at TEXT, source TEXT)")
    for ck, meta_json, cached, src in cache_rows:
        con.execute("INSERT INTO transcript_cache VALUES (?,?,?,?)",
                    (ck, meta_json, cached, src))
    con.commit()
    con.close()
    return tdb


@pytest.fixture(autouse=True)
def log_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ttp, "LOG_ROOT", tmp_path / "runs")
    # reset module-level archive caches so per-test DHT_ARCHIVE_DIRS apply
    monkeypatch.setattr(ttp, "_DHT_DIR_FILES", None)
    monkeypatch.setattr(ttp, "_ARTIVE_CONN_CACHE", {})
    return tmp_path / "runs"


# --------------------------------------------------------------------------
# snowflake / decode primitives (case 4 mechanics)
# --------------------------------------------------------------------------

def test_snowflake_decode_known_epoch():
    assert ttp.ms_to_date(ttp.decode_snowflake_ms(0)) == "2015-01-01"


def test_snowflake_roundtrip_date():
    target_ms = int(datetime(
        2023, 7, 15, 12, 34, 56, tzinfo=timezone.utc).timestamp() * 1000)
    sid = ((target_ms - ttp.DISCORD_EPOCH_MS_OFFSET) << 22) | 12345
    assert ttp.ms_to_date(ttp.decode_snowflake_ms(sid)) == "2023-07-15"


def test_recover_valid_dates_from_fixture(tmp_path):
    ft, lt = 1667752885659, 1684504459125   # 2022-11-06 / 2023-05-19 UTC
    mid = ((ft - ttp.DISCORD_EPOCH_MS_OFFSET) << 22) | 7
    tdb = _mk_transcripts_db(tmp_path, [
        ("dht:c1:100:200", json.dumps({"first_ts": str(ft),
                                       "last_ts": str(lt)}),
         "2026-08-21", "discord"),
        ("da:a1", json.dumps({"message_id": mid}), "2026-08-21",
         "dht-artifact"),
    ])
    rec = ttp.recover_valid_dates(sqlite3.connect(str(tdb)))
    assert rec["dht:c1:100:200"]["valid_start"] == "2022-11-06"
    assert rec["dht:c1:100:200"]["valid_end"] == "2023-05-19"
    assert rec["da:a1"]["method"] == "message_id_snowflake"
    assert rec["da:a1"]["valid_start"] == ttp.ms_to_date(ft)


def test_dht_artifact_url_snowflake_fallback(tmp_path, monkeypatch):
    # synthetic hash ids must never be decoded; the CDN url carries the real
    # attachment snowflake
    monkeypatch.setattr(ttp, "DHT_ARCHIVE_DIRS", ())
    ft = 1038855737989935155
    ts_expected = ttp.ms_to_date(ttp.decode_snowflake_ms(ft))
    tdb = _mk_transcripts_db(tmp_path, [
        ("dht-artifact:some_archive:0:9",
         json.dumps({"message_id": 0,
                     "url": f"https://cdn.discordapp.com/attachments/"
                            f"999/{ft}/1.png"}),
         "2026-08-21", "dht-artifact"),
    ])
    rec = ttp.recover_valid_dates(sqlite3.connect(str(tdb)))
    assert rec["dht-artifact:some_archive:0:9"]["method"] == \
        "url_attachment_snowflake"
    assert rec["dht-artifact:some_archive:0:9"]["approx"] is True
    assert rec["dht-artifact:some_archive:0:9"]["valid_start"] == ts_expected


def test_dht_artifact_raw_archive_bridge_exact(tmp_path, monkeypatch):
    """When the source archive is resolvable and retains the
    attachments->messages bridge, the exact messages timestamp wins."""
    archive_dir = tmp_path / "dht"
    archive_dir.mkdir()
    ts_ms = 1667752909237   # stored column value
    url = ("https://cdn.discordapp.com/attachments/"
           "1038151775095635989/1038855737989935155/1.png")
    aconn = sqlite3.connect(str(archive_dir / "test arch.dht"))
    aconn.execute("CREATE TABLE attachments (attachment_id INTEGER PRIMARY"
                  "_KEY, normalized_url TEXT)")
    aconn.execute("CREATE TABLE message_attachments (message_id INTEGER,"
                  " attachment_id INTEGER, PRIMARY KEY(message_id,"
                  " attachment_id))")
    aconn.execute("CREATE TABLE messages (message_id INTEGER PRIMARY KEY,"
                  " sender_id INTEGER, channel_id INTEGER, text TEXT,"
                  " timestamp INTEGER)")
    aconn.execute("INSERT INTO attachments VALUES (1, ?)", (url,))
    aconn.execute("INSERT INTO messages VALUES (10, 2, 3, 'x', ?)", (ts_ms,))
    aconn.execute("INSERT INTO message_attachments VALUES (10, 1)")
    aconn.commit()
    aconn.close()
    monkeypatch.setattr(ttp, "DHT_ARCHIVE_DIRS", (str(archive_dir),))
    tdb = _mk_transcripts_db(tmp_path, [
        ("dht-artifact:test_arch:0:9",
         json.dumps({"message_id": 0, "url": url}),
         "2026-08-21", "dht-artifact"),
    ])
    rec = ttp.recover_valid_dates(sqlite3.connect(str(tdb)))
    got = rec["dht-artifact:test_arch:0:9"]
    assert got["method"] == "raw_archive_bridge_message_time"
    assert got["approx"] is False
    assert got["valid_start"] == "2022-11-06"


# --------------------------------------------------------------------------
# arms
# --------------------------------------------------------------------------

def test_arm_b_excludes_undated(log_root, tmp_path):
    snap = _mk_snapshot(tmp_path, [
        _eu("u1", "", "2026-08-21", src="discord"),
        _eu("d1", "2026-05-09", "2026-05-10"),
    ], [("e1", "u1"), ("e1", "d1")])
    p = ttp.apply_arm(snap, "b_exclude")
    rows = dict(sqlite3.connect(str(p)).execute(
        "SELECT eu_id, COALESCE(NULLIF(published_at,''),captured_at)"
        " FROM eu").fetchall())
    assert rows["u1"] == ""      # undated removed from observation dating
    assert rows["d1"] == "2026-05-09"   # dated rows untouched


def test_arm_c_recovers_discord_dates(log_root, tmp_path, monkeypatch):
    tdb = _mk_transcripts_db(tmp_path, [
        ("dht:cA:100:150", json.dumps({"first_ts": "1626318000000"}),
         "2026-08-21", "discord")])
    monkeypatch.setattr(ttp, "TRANSCRIPTS_DB", tdb)
    snap = _mk_snapshot(tmp_path, [
        _eu("w1", "", "2026-08-21T00:00:00", src="discord",
            aref="dht:cA:100:150")], [])
    p = ttp.apply_arm(snap, "c_recover")
    got = sqlite3.connect(str(p)).execute(
        "SELECT published_at FROM eu WHERE eu_id='w1'").fetchone()[0]
    assert got == ttp.ms_to_date(1626318000000)


def test_interval_end_moves_observation_date(log_root, tmp_path,
                                             monkeypatch):
    tdb = _mk_transcripts_db(tmp_path, [
        ("dht:cC:1:2", json.dumps({"first_ts": "1570000000000",
                                   "last_ts": "1690000000000"}),
         "2026-08-21", "discord")])
    monkeypatch.setattr(ttp, "TRANSCRIPTS_DB", tdb)
    snap = _mk_snapshot(tmp_path, [
        _eu("iv", "", "2026-08-21", ch="cC", src="discord",
            aref="dht:cC:1:2")], [("e1", "iv")])
    c = ttp.apply_arm(snap, "c_recover")
    d_end = ttp.apply_arm(snap, "d_interval_end")
    qc = sqlite3.connect(str(c)).execute(
        "SELECT published_at FROM eu WHERE eu_id='iv'").fetchone()[0]
    qd = sqlite3.connect(str(d_end)).execute(
        "SELECT published_at FROM eu WHERE eu_id='iv'").fetchone()[0]
    assert qc == "2019-10-02"   # interval start
    assert qd == "2023-07-22"   # interval end


# --------------------------------------------------------------------------
# replay semantics (cases 1, 2, 3, 6, 7)
# --------------------------------------------------------------------------

def _run_replay(snap: Path, arm: str, kasof: bool, **kw):
    p = ttp.apply_arm(snap, arm)
    return ttp.replay_arm(p, knowledge_as_of=kasof, **kw)


def _tl(run):
    return {t["as_of"]: t for t in run["timeline"]}


def test_case1_published_yesterday_ingested_today(log_root, tmp_path):
    snap = _mk_snapshot(tmp_path,
                        [_eu("a1", "2026-08-25", "2026-08-26T05:00:00")],
                        [("e1", "a1")])
    # world-state semantics: valid-time-only counting sees it from its day.
    assert _tl(_run_replay(snap, "a_current", False))
    r_wonly = _run_replay(snap, "a_current", False)
    # knowledge-as-of: must NOT be known before recording (08-26).
    r_kasof = _run_replay(snap, "a_current", True)
    tl_w, tl_k = _tl(r_wonly), _tl(r_kasof)
    assert tl_w["2026-08-25"]["entities_evaluated"] >= 1
    assert tl_k["2026-08-25"]["entities_evaluated"] == 0
    assert tl_k["2026-08-26"]["entities_evaluated"] >= 1


def test_case2_year_old_evidence_ingested_today_needs_both_times(log_root,
                                                                 tmp_path):
    snap = _mk_snapshot(tmp_path,
                        [_eu("old1", "2025-08-20", "2026-08-26T05:00:00")],
                        [("e1", "old1")])
    tl_k = _tl(_run_replay(snap, "a_current", True))
    for d, t in tl_k.items():
        if d < "2026-08-26":
            assert t["entities_evaluated"] == 0


def test_case3_unknown_time_ingested_today_current_vs_exclude(log_root,
                                                              tmp_path):
    snap = _mk_snapshot(tmp_path,
                        [_eu("u1", "", "2026-08-21T01:00:00", src="discord")],
                        [("e1", "u1")])
    a = _run_replay(snap, "a_current", False)
    b = _run_replay(snap, "b_exclude", False)
    assert a["mentions_total"] == 1          # current: enters via captured_at
    assert b["mentions_total"] == 0          # exclude: absent everywhere
    for t in a["timeline"]:
        if t["as_of"] < "2026-08-21":
            assert t["entities_evaluated"] == 0


def test_case6_future_ingestion_no_lookahead_under_bitemporal(log_root,
                                                              tmp_path):
    snap = _mk_snapshot(tmp_path,
                        [_eu("fut1", "2025-01-01", "2030-01-01T00:00:00")],
                        [("e1", "fut1")])
    r_wonly = _run_replay(snap, "a_current", False, end="2026-08-26")
    r_kasof = _run_replay(snap, "a_current", True, end="2026-08-26")
    # documented look-ahead in pure-valid-time replay
    assert r_wonly["mentions_total"] >= 1
    assert any(t["entities_evaluated"] >= 1 for t in r_wonly["timeline"])
    # bitemporal predicate suppresses evidence recorded in the future:
    # no evaluation day may see it, and the valid<recorded gap is reported
    assert all(t["entities_evaluated"] == 0 for t in r_kasof["timeline"])
    assert r_kasof["mentions_where_valid_before_recorded"] >= 1


def test_case7_undated_bulk_capture_fabricates_burst(log_root, tmp_path,
                                                     monkeypatch):
    """The live production failure mode: messages really sent across
    2021-2024 arrive together on one bulk-capture day. Current behaviour
    fabricates an Aug-2026 burst; recovered dates do not."""
    true_days = ["2021-03-04", "2022-06-15", "2023-11-02", "2024-02-29",
                 "2024-08-01"]
    eus, cache_rows = [], []
    for i, td in enumerate(true_days):
        ts_ms = int(datetime.strptime(td, "%Y-%m-%d").replace(
            tzinfo=timezone.utc).timestamp() * 1000)
        ck = f"dht:cB:{1000 + i}:{1010 + i}"
        eus.append(_eu(f"w{i}", "", "2026-08-22T01:00:00", ch="cB",
                       src="discord", aref=ck))
        cache_rows.append((ck, json.dumps({"first_ts": str(ts_ms)}),
                           "2026-08-22", "discord"))
    snap = _mk_snapshot(tmp_path, eus,
                        [("ent", f"w{i}") for i in range(len(eus))])
    tdb = _mk_transcripts_db(tmp_path, cache_rows)
    monkeypatch.setattr(ttp, "TRANSCRIPTS_DB", tdb)

    a = _run_replay(snap, "a_current", False, start="2026-08-10")
    c = _run_replay(snap, "c_recover", False, start="2026-08-10")
    ma = a["final_entity_metrics"]["ent"]
    mc = c["final_entity_metrics"]["ent"]
    # current: all five observations stamp onto capture days -> heavy recent
    # support; v2 signal fires
    assert ma["v1_recent_count"] == 5
    assert ma["v2_k_recent"] == 5
    assert ma["v2_positive"] is True
    # recovered: observations sit at their true years; zero recent-window
    # support at any 2026 date -> no fabricated burst
    assert mc["v1_recent_count"] == 0
    assert mc["v2_k_recent"] == 0
    assert mc["v2_positive"] is False


def test_case7b_recovered_time_keeps_in_window_counting(log_root,
                                                        tmp_path,
                                                        monkeypatch):
    """A message truly posted in May 2026 that arrives late (bulk capture):
    recovery must move it OUT of the fabricated recency AND keep it counted
    inside the baseline window — exclusion (arm B) would drop it entirely."""
    true_day = "2026-05-05"          # inside the 90d baseline window of the
                                     # as_of=2026-08-26 evaluation, outside
                                     # the 30d recent window
    ts_ms = int(datetime.strptime(true_day, "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp() * 1000)
    tdb = _mk_transcripts_db(tmp_path, [
        ("dht:cD:9:9", json.dumps({"first_ts": str(ts_ms)}),
         "2026-08-22", "discord")])
    monkeypatch.setattr(ttp, "TRANSCRIPTS_DB", tdb)
    snap = _mk_snapshot(tmp_path,
                        [_eu("h1", "", "2026-08-22T01:00:00", ch="cD",
                             src="discord", aref="dht:cD:9:9")],
                        [("e1", "h1")])
    a = _run_replay(snap, "a_current", False, start="2026-08-01",
                    end="2026-08-26")
    b = _run_replay(snap, "b_exclude", False, start="2026-08-01",
                    end="2026-08-26")
    c = _run_replay(snap, "c_recover", False, start="2026-08-01",
                    end="2026-08-26")
    # A: falsely recent; B: dropped everywhere; C: correct bucket.
    assert a["final_entity_metrics"]["e1"]["v1_recent_count"] == 1
    assert b["final_entity_metrics"].get("e1") is None
    m_c = c["final_entity_metrics"]["e1"]
    assert m_c["v1_recent_count"] == 0
    assert m_c["v1_baseline_count"] == 1
