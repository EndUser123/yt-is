"""Offline tests for the typed personal-intelligence drill-down."""

import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

from ef import personal_graph
from ef.grounded_source import bind_evidence_clusters, from_transcript_result
from ef.warm_query_service import (
    Handler,
    _interest_detail,
    _render_interest_page,
)


def _source():
    return from_transcript_result(
        "video-detail-1",
        "https://example.test/watch?v=video-detail-1",
        SimpleNamespace(
            transcript="The exact supporting transcript span.",
            video_title="Grounded detail source",
            raw_lang="en",
            source="test-transcript",
            source_stage=1,
            was_translated=False,
        ),
    )


def _seed(path):
    conn = personal_graph.connect(path)
    source = bind_evidence_clusters(_source(), [7])
    personal_graph.store_grounded_source_artifact(conn, source)
    interest_id = "int-detail"
    goal_id = "goal-detail"
    conn.execute(
        "INSERT INTO goals (goal_id, statement, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (goal_id, "Make a defensible decision", "open", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO interests (interest_id, name, kind, temporal_state, "
        "stance, confidence, observed_vs_inferred, goal_id, evidence_json, "
        "exclusions_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            interest_id,
            "Decision quality",
            "domain",
            "active",
            "learning",
            0.84,
            "inferred",
            goal_id,
            json.dumps({"cluster_ids": [7], "evidence_summary": "support"}),
            json.dumps({}),
            "2026-01-02",
        ),
    )
    conn.execute(
        "INSERT INTO information_needs (need_id, statement, interest_id, "
        "goal_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("need-detail", "Compare the available options", interest_id, goal_id,
         "open", "2026-01-01", "2026-01-02"),
    )
    conn.execute(
        "INSERT INTO questions (question_id, text, status, interest_id, "
        "opened_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("question-detail", "What evidence would change the decision?", "open",
         interest_id, "2026-01-01", "2026-01-02"),
    )
    conn.execute(
        "INSERT INTO evidence_links "
        "(src_kind, src_id, dst_kind, dst_id, relation, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("evidence_cluster", "7", "interest", interest_id, "supports", "2026-01-02"),
    )
    conn.execute(
        "INSERT INTO evidence_links "
        "(src_kind, src_id, dst_kind, dst_id, relation, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("source_artifact", source.source_id, "interest", interest_id,
         "supports", "2026-01-02"),
    )
    conn.commit()
    conn.close()


def test_interest_detail_is_read_only_and_collects_typed_provenance(tmp_path):
    db = tmp_path / "catalog.sqlite"
    _seed(db)

    detail = _interest_detail("int-detail", db)

    assert detail["goal"]["statement"] == "Make a defensible decision"
    assert detail["information_needs"][0]["statement"] == "Compare the available options"
    assert detail["questions"][0]["text"] == "What evidence would change the decision?"
    assert detail["support_clusters"] == ["7"]
    assert detail["sources"][0]["source_id"] == "video-detail-1"
    assert detail["sources"][0]["spans"][0]["text"] == (
        "The exact supporting transcript span.")

    conn = personal_graph.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM interests").fetchone()[0] == 1
    conn.close()


def test_interest_detail_page_labels_uncertainty_and_source_evidence(tmp_path,
                                                                      monkeypatch):
    db = tmp_path / "catalog.sqlite"
    _seed(db)
    monkeypatch.setattr(personal_graph, "CATALOG", db)

    html = _render_interest_page("int-detail")

    assert "Decision quality" in html
    assert "inferred" in html
    assert "confidence: 0.84" in html
    assert "Make a defensible decision" in html
    assert "The exact supporting transcript span." in html
    assert "https://example.test/watch?v=video-detail-1" in html
    assert "Source artifacts are provenance records" in html


def test_interest_detail_returns_none_for_unknown_id(tmp_path):
    db = tmp_path / "catalog.sqlite"
    conn = personal_graph.connect(db)
    conn.close()

    assert _interest_detail("does-not-exist", db) is None


def test_http_route_serves_typed_interest_and_rejects_bad_path(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer

    db = tmp_path / "catalog.sqlite"
    _seed(db)
    monkeypatch.setattr(personal_graph, "CATALOG", db)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/interest/int-detail", timeout=5) as response:
            assert response.status == 200
            body = response.read().decode("utf-8")
        assert "Decision quality" in body
        assert "The exact supporting transcript span." in body

        try:
            urlopen(base + "/interest/", timeout=5)
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("malformed interest path was accepted")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
