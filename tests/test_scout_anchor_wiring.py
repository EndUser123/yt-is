"""Scout DB-resolution + anchor-starvation diagnostics (read-only).

Proves the SCOUT_WIRING_GAP fix: build_scout_plan(None) resolves through
the single canonical seam ef.personal_graph.get_catalog_path(), explicit
overrides win, empty anchor tables fail VISIBLY (anchor_state/degraded_
reason) instead of crashing or fabricating anchors. No network; no
semantic-table mutation."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ef import horizon_scout as hs

_REPO = Path(__file__).resolve().parents[1]


def _make_graph_db(path, interest_name=None,
                   goal_statement=None, need_statement=None):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE interests (
          interest_id TEXT PRIMARY KEY, name TEXT, kind TEXT,
          temporal_state TEXT, stance TEXT
        );
        CREATE TABLE goals (goal_id TEXT PRIMARY KEY, statement TEXT, status TEXT);
        CREATE TABLE information_needs (
          need_id TEXT PRIMARY KEY, statement TEXT, interest_id TEXT,
          goal_id TEXT, status TEXT
        );
        """
    )
    if interest_name:
        conn.execute(
            "INSERT INTO interests VALUES (?,?,?,?,?)",
            ("i1", interest_name, "domain", "durable", "learning"))
    if goal_statement:
        conn.execute("INSERT INTO goals VALUES (?,?,?)",
                     ("g1", goal_statement, "open"))
    if need_statement:
        conn.execute(
            "INSERT INTO information_needs VALUES (?,?,?,?,?)",
            ("n1", need_statement, "i1" if interest_name else None,
             "g1" if goal_statement else None, "open"))
    conn.commit()
    conn.close()
    return str(path)


# 1. no-argument resolution flows through the single canonical seam,
#    ef.personal_graph.CATALOG, and 7. populated Interests produce
#    known-domain planning.
def test_none_resolves_through_personal_graph_catalog_seam(tmp_path, monkeypatch):
    import ef.personal_graph as pg

    fixture = _make_graph_db(tmp_path / "canonical.sqlite",
                             interest_name="replicated log consensus")
    monkeypatch.setattr(pg, "CATALOG", Path(fixture))
    plan = hs.build_scout_plan(None)
    assert plan.queries
    assert any(q.exploration == "known_domain"
               and "replicated log consensus" in q.query for q in plan.queries)
    assert plan.anchor_state == "READY"
    # a missing canonical DB degrades visibly instead of raising TypeError
    monkeypatch.setattr(pg, "CATALOG", tmp_path / "missing.sqlite")
    plan2 = hs.build_scout_plan(None)
    assert plan2.anchor_state == "EMPTY"
    assert plan2.degraded_reason == "NO_ACCEPTED_PERSONAL_ANCHORS"


# 2. explicit --graph-db path wins over the default seam.
def test_explicit_override_wins_over_default(tmp_path, monkeypatch):
    import ef.personal_graph as pg

    explicit = _make_graph_db(tmp_path / "explicit.sqlite",
                              interest_name="edge cluster routing")
    decoy = _make_graph_db(tmp_path / "decoy.sqlite",
                           interest_name="decoy domain anchor")
    monkeypatch.setattr(pg, "CATALOG", Path(decoy))
    plan = hs.build_scout_plan(explicit)
    texts = " | ".join(q.query for q in plan.queries)
    assert "edge cluster routing" in texts
    assert "decoy domain anchor" not in texts


# 3+4. empty canonical anchor tables: no crash, explicit EMPTY diagnostic.
def test_empty_anchors_do_not_crash_and_report_empty(tmp_path):
    db = _make_graph_db(tmp_path / "empty.sqlite")  # schema only, zero rows
    plan = hs.build_scout_plan(db)
    assert plan.queries  # intentional wildcard plan still produced
    assert plan.anchor_state == "EMPTY"
    assert plan.degraded_reason == "NO_ACCEPTED_PERSONAL_ANCHORS"


# 4b/11. diagnostics survive serialization into the scout-plan artifact.
def test_serialized_plan_preserves_anchor_diagnostics(tmp_path):
    db = _make_graph_db(tmp_path / "empty2.sqlite")
    d = hs.build_scout_plan(db).to_dict()
    assert d["anchor_state"] == "EMPTY"
    assert d["degraded_reason"] == "NO_ACCEPTED_PERSONAL_ANCHORS"
    ready = hs.build_scout_plan(
        _make_graph_db(tmp_path / "ready.sqlite",
                       interest_name="some durable topic")).to_dict()
    assert ready["anchor_state"] == "READY"
    assert ready["degraded_reason"] is None


# 5. wildcard anti-filter-bubble exploration remains available when EMPTY.
def test_empty_anchor_plan_still_exploratory_not_fake_specific(tmp_path):
    db = _make_graph_db(tmp_path / "empty3.sqlite")
    plan = hs.build_scout_plan(db, max_queries=12)
    assert {q.exploration for q in plan.queries} == {"wildcard"}
    assert {q.query for q in plan.queries} <= set(hs.WILDCARD_QUERIES)


# 6. no fake personal anchors are fabricated on EMPTY.
def test_no_fabricated_anchors_on_empty(tmp_path):
    db = _make_graph_db(tmp_path / "empty4.sqlite")
    plan = hs.build_scout_plan(db, max_queries=12)
    for q in plan.queries:
        assert q.origin_kind == "wildcard"
        assert q.origin_id is None


# 7 (full). populated fixture Interests produce normal known-domain planning.
def test_populated_interests_known_domain_planning(tmp_path):
    db = _make_graph_db(tmp_path / "ints.sqlite", interest_name="vector databases")
    plan = hs.build_scout_plan(db, max_queries=12)
    known = [q for q in plan.queries if q.exploration == "known_domain"]
    assert known and all("vector databases" in q.query for q in known)
    assert plan.anchor_state == "READY" and plan.degraded_reason is None


# 8. Goals/InformationNeeds produce intended adjacent planning.
def test_goals_and_needs_produce_adjacent_planning(tmp_path):
    db = _make_graph_db(tmp_path / "goals.sqlite", interest_name=None,
                        goal_statement="cut coordination overhead in replays",
                        need_statement="lighter consensus for edge clusters")
    plan = hs.build_scout_plan(db, max_queries=12)
    adjacent = [q for q in plan.queries if q.exploration == "adjacent"]
    assert adjacent
    joined = " | ".join(q.query for q in adjacent)
    assert "coordination overhead" in joined or "lighter consensus" in joined


# 9. planning performs zero network calls (default seam AND explicit path).
def test_planning_makes_no_network_calls(tmp_path, monkeypatch):
    import ef.personal_graph as pg

    def boom(*a, **k):
        raise AssertionError("planning opened a socket")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("socket.create_connection", boom)
    explicit = _make_graph_db(tmp_path / "n1.sqlite")
    assert hs.build_scout_plan(explicit).queries
    monkeypatch.setattr(pg, "CATALOG",
                        Path(_make_graph_db(tmp_path / "n2.sqlite")))
    assert hs.build_scout_plan(None).queries


# 10. planning does not mutate semantic tables.
def test_planning_is_read_only_on_semantic_tables(tmp_path):
    db = _make_graph_db(tmp_path / "ro.sqlite", interest_name="immutable check")

    def snapshot():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            counts = tuple(conn.execute(
                f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("interests", "goals", "information_needs"))
            rows = conn.execute(
                "SELECT * FROM interests ORDER BY interest_id").fetchall()
            return counts, rows
        finally:
            conn.close()

    before = snapshot()
    hs.build_scout_plan(db)
    assert snapshot() == before


# CLI contract: scout-plan with no DB argument resolves the canonical
# catalog (ef.personal_graph.CATALOG), does not crash on empty anchors,
# reports degraded status, and writes it into the serialized artifact.
def test_cli_scout_plan_default_resolution_reports_empty(tmp_path, monkeypatch, capsys):
    import ef.personal_graph as pg
    import importlib.util

    fixture = _make_graph_db(tmp_path / "cli-canonical.sqlite")  # zero rows
    monkeypatch.setattr(pg, "CATALOG", Path(fixture))
    spec = importlib.util.spec_from_file_location(
        "discover_concepts", _REPO / "scripts" / "discover_concepts.py")
    dc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dc)

    artifact_dir = tmp_path / "artifacts"
    rc = dc.main(["--artifact-dir", str(artifact_dir), "scout-plan"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING anchor_state=EMPTY" in out
    plan_files = list(artifact_dir.glob("**/scout-plan.json"))
    assert len(plan_files) == 1
    plan_json = json.loads(plan_files[0].read_text(encoding="utf-8"))
    assert plan_json["anchor_state"] == "EMPTY"
    assert plan_json["degraded_reason"] == "NO_ACCEPTED_PERSONAL_ANCHORS"

    # ...and an explicit --graph-db overrides the default cleanly.
    explicit_db = _make_graph_db(tmp_path / "cli-explicit.sqlite",
                                 interest_name="explicit wins")
    explicit_dir = tmp_path / "artifacts-explicit"
    rc2 = dc.main(["--graph-db", explicit_db,
                   "--artifact-dir", str(explicit_dir), "scout-plan"])
    assert rc2 == 0
    assert "anchor_state=EMPTY" not in capsys.readouterr().out
