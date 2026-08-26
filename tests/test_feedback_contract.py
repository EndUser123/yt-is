"""Impression + feedback-event contract tests (2026-08-26 hardening).

Covers: immutable events, idempotent retries, candidate-set
reconstruction, rank/policy attribution, workflow-state separation,
POST-only HTTP semantics, transaction rollback, concurrent duplicates,
legacy-table preservation, and no silent Interest-inference writes.

All tests run against a temp SQLite path via YTIS_FEEDBACK_DB — the
production catalog is never touched.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from ef import personal_graph as pg
from ef import warm_query_service as wqs


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "catalog.sqlite"
    monkeypatch.setenv("YTIS_FEEDBACK_DB", str(path))
    # initialize schema
    pg.connect(str(path)).close()
    return str(path)


def _batch(items=None, db_path=None):
    items = items or [
        {"item_kind": "cluster", "item_id": "cluster:1",
         "item_label": "topic a", "why_surfaced": "emerging"},
        {"item_kind": "doc", "item_id": "video:abc",
         "item_label": "some title", "why_surfaced": "recent_doc"},
    ]
    return pg.record_candidate_set(
        "today", "mechanical-clusters-recency", "v1", items,
        provenance="test", db_path=db_path)


# ---------------------------------------------------------------- contract

def test_unique_impression_ids(db):
    b1, b2 = _batch(), _batch()
    ids = b1["impression_ids"] + b2["impression_ids"]
    assert len(ids) == len(set(ids)) == 4


def test_unique_feedback_event_ids(db):
    r1 = pg.record_feedback_event("today", "cluster", "cluster:1",
                                  "useful", db_path=db)
    r2 = pg.record_feedback_event("today", "cluster", "cluster:1",
                                  "investigate", db_path=db)
    assert r1["feedback_event_id"] != r2["feedback_event_id"]


def test_explicit_idempotency_key_dedupes(db):
    r1 = pg.record_feedback_event("today", "cluster", "cluster:1",
                                  "useful", idempotency_key="k1",
                                  db_path=db)
    r2 = pg.record_feedback_event("today", "cluster", "cluster:1",
                                  "useful", idempotency_key="k1",
                                  db_path=db)
    assert r1["duplicate"] is False and r2["duplicate"] is True
    assert r1["feedback_event_id"] == r2["feedback_event_id"]
    conn = pg.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback_events"
                        ).fetchone()[0] == 1
    conn.close()


def test_derived_key_retry_within_window_dedupes(db, monkeypatch):
    t = [1_800_000_000]
    monkeypatch.setattr(pg.time, "time", lambda: t[0])
    r1 = pg.record_feedback_event("today", "cluster", "cluster:1",
                                  "useful", db_path=db)
    t[0] += 5  # same 60s bucket -> retry
    r2 = pg.record_feedback_event("today", "cluster", "cluster:1",
                                  "useful", db_path=db)
    assert r2["duplicate"] is True
    t[0] += 120  # a genuinely later moment -> distinct event
    r3 = pg.record_feedback_event("today", "cluster", "cluster:1",
                                  "useful", db_path=db)
    assert r3["duplicate"] is False
    conn = pg.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback_events"
                        ).fetchone()[0] == 2
    conn.close()


def test_multiple_legitimate_events_over_time(db):
    for v in ("investigate", "acted_on"):
        r = pg.record_feedback_event("today", "doc", "video:abc", v,
                                     db_path=db)
        assert r["ok"] and r["duplicate"] is False
    conn = pg.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback_events"
                        ).fetchone()[0] == 2
    conn.close()


def test_candidate_set_reconstruction_and_rank(db):
    items = [{"item_kind": "doc", "item_id": f"video:{i}",
              "item_label": f"t{i}"} for i in range(5)]
    batch = _batch(items)
    rows = pg.get_impressions_for_candidate_set(
        batch["candidate_set_id"])
    assert [r["item_id"] for r in rows] == [f"video:{i}" for i in range(5)]
    assert [r["rank_position"] for r in rows] == [1, 2, 3, 4, 5]
    assert all(r["ranking_policy"] == "mechanical-clusters-recency" and
               r["ranking_policy_version"] == "v1" for r in rows)
    conn = pg.connect(db)
    cs = conn.execute("SELECT surface, ranking_policy, "
                      "ranking_policy_version, items_json "
                      "FROM candidate_sets WHERE candidate_set_id = ?",
                      (batch["candidate_set_id"],)).fetchone()
    assert cs["surface"] == "today"
    assert json.loads(cs["items_json"])[2]["rank_position"] == 3
    conn.close()


def test_unknown_optional_fields_stay_null(db):
    batch = _batch()
    conn = pg.connect(db)
    row = dict(conn.execute(
        "SELECT * FROM impressions WHERE impression_id = ?",
        (batch["impression_ids"][0],)).fetchone())
    conn.close()
    for field in ("score", "origin_interest_id", "actor_context",
                  "world_signal_json", "personal_relevance_json",
                  "experiment_id", "propensity"):
        assert row[field] is None, field


def test_unknown_impression_id_rejected(db):
    r = pg.record_feedback_event("today", "cluster", "cluster:1",
                                 "useful", impression_id="imp_missing",
                                 db_path=db)
    assert r["ok"] is False and "impression" in r["error"]


# ------------------------------------------------------- workflow state

def test_workflow_state_separate_from_event_history(db):
    pg.record_feedback_event("today", "doc", "video:abc", "investigate",
                             db_path=db)
    pg.record_feedback_event("today", "doc", "video:abc", "save",
                             db_path=db)
    st = pg.get_workflow_state("doc", "video:abc", db_path=db)
    assert st["state"] == "saved" and st["prior_state"] == "investigate"
    conn = pg.connect(db)
    # both events remain in history; one state row
    assert conn.execute("SELECT COUNT(*) FROM feedback_events"
                        ).fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM item_workflow_state"
                        ).fetchone()[0] == 1
    conn.close()


def test_evaluation_verdicts_do_not_transition(db):
    for v in ("useful", "known_already", "wrong_inference",
              "more_like", "less_like"):
        r = pg.record_feedback_event("today", "doc", "video:abc", v,
                                     db_path=db)
        assert r["workflow_state"] is None
    assert pg.get_workflow_state("doc", "video:abc", db_path=db) is None


def test_duplicate_does_not_retransition(db):
    pg.record_feedback_event("today", "doc", "video:abc", "save",
                             idempotency_key="k", db_path=db)
    st1 = pg.get_workflow_state("doc", "video:abc", db_path=db)
    # true retry: same payload, same key
    r = pg.record_feedback_event("today", "doc", "video:abc", "save",
                                 idempotency_key="k", db_path=db)
    assert r["duplicate"] is True
    st2 = pg.get_workflow_state("doc", "video:abc", db_path=db)
    assert st2 == st1  # duplicate retry: no state churn, no history rewrite


def test_idempotency_key_reuse_with_different_payload_rejected(db):
    pg.record_feedback_event("today", "doc", "video:abc", "save",
                             idempotency_key="k", db_path=db)
    r = pg.record_feedback_event("today", "doc", "video:abc",
                                 "investigate", idempotency_key="k",
                                 db_path=db)
    assert r["ok"] is False and "reuse" in r["error"]


def test_feedback_never_touches_interest_inference(db):
    conn = pg.connect(db)
    conn.execute(
        "INSERT INTO interests (interest_id, name, kind) "
        "VALUES ('int_x', 'probe', 'topic')")
    conn.commit()
    conn.close()
    pg.record_feedback_event("today", "interest", "int_x", "not_interested",
                             db_path=db)
    conn = pg.connect(db)
    row = dict(conn.execute("SELECT * FROM interests "
                            "WHERE interest_id = 'int_x'").fetchone())
    conn.close()
    assert row["name"] == "probe" and row["temporal_state"] is None


# ------------------------------------------------------------- migration

def test_legacy_rows_preserved_and_not_extended(db):
    assert pg.record_feedback("today", "cluster", "cluster:9",
                              "useful") is True
    before = pg.connect(db).execute(
        "SELECT COUNT(*) FROM feedback").fetchone()[0]
    for v in ("useful", "investigate", "acted_on"):
        pg.record_feedback_event("today", "cluster", "cluster:9", v,
                                 db_path=db)
    conn = pg.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback"
                        ).fetchone()[0] == before  # legacy table frozen
    assert pg.feedback_summary() == {"useful": 1}  # legacy history intact
    conn.close()


# ----------------------------------------------------------- transactions

def test_event_and_state_transition_atomic(db):
    conn = pg.connect(db)
    # force the state-write leg to fail; the event insert must roll back
    conn.execute("CREATE TRIGGER abort_state BEFORE INSERT ON "
                 "item_workflow_state BEGIN SELECT RAISE(ABORT, 'no'); END")
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.IntegrityError):
        pg.record_feedback_event("today", "doc", "video:abc", "save",
                                 db_path=db)
    conn = pg.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback_events"
                        ).fetchone()[0] == 0
    conn.close()


def test_concurrent_duplicate_submission(db):
    results = []
    barrier = threading.Barrier(4)

    def submit():
        barrier.wait()
        results.append(pg.record_feedback_event(
            "today", "cluster", "cluster:1", "useful",
            idempotency_key="race", db_path=db))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r["ok"] for r in results)
    assert sum(1 for r in results if not r["duplicate"]) == 1
    conn = pg.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback_events"
                        ).fetchone()[0] == 1
    conn.close()


# ------------------------------------------------------------- HTTP layer

@pytest.fixture
def server(db):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), wqs.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _post(base, payload, headers=None):
    req = urllib.request.Request(
        base + "/feedback", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def test_http_get_does_not_mutate(server, db):
    req = urllib.request.Request(server + "/feedback?v=useful&kind=doc"
                                 "&id=x&surface=today")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 405
    conn = pg.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback_events"
                        ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM feedback"
                        ).fetchone()[0] == 0
    conn.close()


def test_http_post_records_with_impression(server, db):
    batch = _batch()
    status, body = _post(server, {
        "surface": "today", "kind": "doc", "id": "video:abc",
        "v": "investigate", "impression_id": batch["impression_ids"][1]})
    assert status == 200 and body["ok"] and not body["duplicate"]
    conn = pg.connect(db)
    row = conn.execute("SELECT impression_id, source_route FROM "
                       "feedback_events").fetchone()
    assert row["impression_id"] == batch["impression_ids"][1]
    assert row["source_route"] == "POST /feedback"
    conn.close()
    # retry with the server response's idempotency key -> duplicate
    status, body2 = _post(server, {
        "surface": "today", "kind": "doc", "id": "video:abc",
        "v": "investigate", "impression_id": batch["impression_ids"][1],
        "idempotency_key": body["idempotency_key"]})
    assert body2["duplicate"] is True


def test_http_post_invalid_verdict_400(server):
    status = None
    try:
        _post(server, {"surface": "today", "kind": "doc", "id": "x",
                       "v": "delightful"})
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 400


def test_http_cross_origin_rejected(server, db):
    status = None
    try:
        _post(server, {"surface": "today", "kind": "doc", "id": "x",
                       "v": "useful"},
              headers={"Origin": "http://evil.example"})
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 403
    conn = pg.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback_events"
                        ).fetchone()[0] == 0
    conn.close()


# ------------------------------------------------- evaluation annotations

def test_annotation_preserves_raw_event_and_is_additive(db):
    r = pg.record_feedback_event("today", "cluster", "cluster:1",
                                 "wrong_inference", db_path=db)
    fid = r["feedback_event_id"]
    before = pg.get_feedback_event(fid, db_path=db)
    a = pg.annotate_feedback_event(
        fid, "test_probe", reason="live contract verification", db_path=db)
    assert a["ok"] and not a["duplicate"]
    after = pg.get_feedback_event(fid, db_path=db)
    # raw event fields byte/semantically intact; annotation arrived additively
    for k in before:
        if k != "annotations":
            assert after[k] == before[k], k
    assert len(after["annotations"]) == 1
    assert after["annotations"][0]["annotation_type"] == "test_probe"
    assert after["annotations"][0]["exclude_from_evaluation"] == 1


def test_evaluation_read_excludes_annotated_probe(db):
    r = pg.record_feedback_event("today", "cluster", "cluster:1",
                                 "wrong_inference", db_path=db)
    probe = r["feedback_event_id"]
    real = pg.record_feedback_event("today", "doc", "video:abc",
                                    "useful", db_path=db)
    pg.annotate_feedback_event(probe, "test_probe", db_path=db)
    rows = pg.feedback_events_for_evaluation(db_path=db)
    ids = [e["feedback_event_id"] for e in rows]
    assert ids == [real["feedback_event_id"]]
    # audit mode: everything back, annotated row flagged
    audit = pg.feedback_events_for_evaluation(include_excluded=True,
                                              db_path=db)
    assert len(audit) == 2
    by_id = {e["feedback_event_id"]: e for e in audit}
    assert by_id[probe]["excluded"] == 1
    assert by_id[real["feedback_event_id"]]["excluded"] == 0
    # raw single-event access still sees the probe
    assert pg.get_feedback_event(probe, db_path=db)["verdict"] \
        == "wrong_inference"


def test_annotation_idempotent_and_conflicting(db):
    r = pg.record_feedback_event("today", "cluster", "cluster:1",
                                 "useful", db_path=db)
    fid = r["feedback_event_id"]
    a1 = pg.annotate_feedback_event(fid, "test_probe", reason="x",
                                    db_path=db)
    a2 = pg.annotate_feedback_event(fid, "test_probe", reason="x",
                                    db_path=db)
    assert a1["ok"] and a2["ok"] and a2["duplicate"] is True
    assert a1["annotation_id"] == a2["annotation_id"]
    conflict = pg.annotate_feedback_event(fid, "test_probe", reason="y",
                                          db_path=db)
    assert conflict["ok"] is False and "reuse" in conflict["error"]
    # second annotation type on the same event is legitimate
    a3 = pg.annotate_feedback_event(fid, "operator_reviewed", db_path=db)
    assert a3["ok"] and not a3["duplicate"]
    unknown = pg.annotate_feedback_event("fe_missing", "test_probe",
                                         db_path=db)
    assert unknown["ok"] is False and "unknown" in unknown["error"]


def test_annotation_does_not_alter_workflow_state(db):
    r = pg.record_feedback_event("today", "doc", "video:abc", "save",
                                 db_path=db)
    st1 = pg.get_workflow_state("doc", "video:abc", db_path=db)
    pg.annotate_feedback_event(r["feedback_event_id"], "test_probe",
                               db_path=db)
    st2 = pg.get_workflow_state("doc", "video:abc", db_path=db)
    assert st2 == st1
    ev = pg.get_feedback_event(r["feedback_event_id"], db_path=db)
    assert ev["verdict"] == "save"


def test_unannotated_feedback_remains_in_evaluation_default(db):
    keep = []
    for i in range(3):
        r = pg.record_feedback_event("today", "doc", f"video:{i}",
                                     "useful", db_path=db)
        keep.append(r["feedback_event_id"])
    pg.annotate_feedback_event(keep[0], "test_probe", db_path=db)
    rows = pg.feedback_events_for_evaluation(item_kind="doc", db_path=db)
    # same-second events share an occurred_at value; compare as a set
    assert {e["feedback_event_id"] for e in rows} == set(keep[1:])
