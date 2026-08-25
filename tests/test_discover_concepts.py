"""Open-world discovery pipeline tests — the discriminating root-cause
test (packet §25) plus CLI spend-gate/artifact behavior.

All transport is mocked; all state lives in tmp_path SQLite. The
fictional "NebulaMesh Runtime" is this packet's OWN synthetic example —
no real-world concept is named anywhere here.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ef import concept_registry as cr  # noqa: E402
from ef import horizon_scout as hs  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "discover_concepts", REPO / "scripts" / "discover_concepts.py")
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)


DOMAIN_INTEREST_ID = "int_distributed_computing"


@pytest.fixture
def graph_db(tmp_path):
    """Registry + minimal personal-graph tables in one temp DB."""
    path = str(tmp_path / "registry.sqlite")
    conn = cr.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS interests (interest_id TEXT PRIMARY KEY,"
        " name TEXT, kind TEXT, temporal_state TEXT, stance TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS goals (goal_id TEXT PRIMARY KEY,"
        " statement TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS information_needs (need_id TEXT"
        " PRIMARY KEY, statement TEXT, interest_id TEXT)")
    conn.execute(
        "INSERT OR REPLACE INTO interests VALUES (?,?,?,?,?)",
        (DOMAIN_INTEREST_ID, "distributed computing", "domain", "durable",
         "learning"))
    conn.commit()
    conn.close()
    return path


def _registry(path):
    return cr.connect(path)


REPO_RESULT = {
    "title": "NebulaMesh Runtime",
    "url": "https://github.com/example/NebulaMesh",
    "snippet": "A new distributed computing runtime with mesh scheduling.",
}


def _fake_mcp(backends):
    """Returns results only for category queries about the broad domain;
    the repository name comes from the RESULT, never from a query."""
    calls = {"n": 0}

    def mcp_call(tool, arguments):
        calls["n"] += 1
        assert tool in ("query", "search_all")
        q = arguments["search_query"].casefold()
        if "distributed computing" not in q:
            return []
        out = []
        for backend in backends:
            out.append(dict(REPO_RESULT, backend=backend))
        return out

    mcp_call.calls = calls
    return mcp_call


def test_open_world_unseen_concept_root_cause(graph_db, tmp_path):
    conn = _registry(graph_db)
    try:
        # 0. The registry has NEVER heard of the concept.
        assert cr.resolve_alias(conn, "NebulaMesh Runtime") is None
        assert conn.execute(
            "SELECT COUNT(*) FROM concepts WHERE lower(canonical_name) LIKE "
            "'%nebulamesh%'").fetchone()[0] == 0

        # 1. Scout plan contains ONLY broad/category terms — the unknown
        #    name cannot appear because it exists nowhere in the system.
        plan = hs.build_scout_plan(graph_db, max_queries=12)
        for q in plan.queries:
            assert "nebulamesh" not in q.query.casefold()
        assert any("distributed computing" in q.query.casefold()
                   for q in plan.queries)
        explorations = {q.exploration for q in plan.queries}
        assert "adjacent" in explorations and "wildcard" in explorations

        # 2. Mocked broad horizon search returns the unseen repository.
        mcp = _fake_mcp(["brave"])
        results = hs.run_scout(plan, mcp_call=mcp, allow_search=True)
        assert any(r.get("url") == REPO_RESULT["url"] for r in results
                   if "url" in r)
        usable = [r for r in results if "url" in r]

        # 3. Candidate is created; name derives from the RESULT URL.
        ingest = hs.ingest_external_results(conn, usable, run_id="scout_t1")
        assert ingest["concepts"] >= 1
        cid = cr.concept_identity_id("repository", "example/nebulamesh")
        concept = cr.get_concept(conn, cid)
        assert concept is not None
        assert concept["canonical_name"] == "example/nebulamesh"
        assert concept["lifecycle_state"] == "candidate"
        assert concept["user_relationship"] == "unknown"

        # 4. Independent later observations accrue onto the SAME concept.
        for backends in (["exa"], ["ddg", "brave"]):
            plan2 = hs.build_scout_plan(graph_db, max_queries=12)
            mcp2 = _fake_mcp(backends)
            results2 = hs.run_scout(plan2, mcp_call=mcp2, allow_search=True)
            hs.ingest_external_results(
                conn, [r for r in results2 if "url" in r],
                run_id="scout_t2")
        obs = conn.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT source_id) s FROM "
            "concept_observations WHERE concept_id=?", (cid,)).fetchone()
        assert obs["n"] >= 3 and obs["s"] >= 2

        # 5. Mechanical trend policy promotes candidate -> emerging;
        #    user_relationship stays 'unknown' (attention != interest).
        promos = dc.promote_on_recurrence(conn)
        assert any(p["concept_id"] == cid and p["promoted"]
                   for p in promos["decisions"])
        assert cr.get_concept(conn, cid)["lifecycle_state"] == "emerging"
        assert cr.get_concept(conn, cid)["user_relationship"] == "unknown"

        # 6. Concept is related to the broad durable domain through the
        #    query-origin provenance wiring.
        links = dc._link_origins(conn, plan, usable)
        assert any(l["concept_id"] == cid
                   and l["interest_id"] == DOMAIN_INTEREST_ID
                   for l in links)
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_interest_links WHERE concept_id=? "
            "AND interest_id=? AND method='semantic'",
            (cid, DOMAIN_INTEREST_ID)).fetchone()[0] == 1

        # 7. No hard-coded candidate-name knowledge in production code.
        for prod in list((REPO / "ef").glob("*.py")) + [
                REPO / "scripts" / "discover_concepts.py"]:
            assert "nebulamesh" not in prod.read_text(
                encoding="utf-8", errors="replace").casefold(), \
                f"production file {prod.name} hard-codes the example"
    finally:
        conn.close()


def test_scout_run_spend_gate(graph_db, capsys):
    assert dc.main(["--db", graph_db, "--artifact-dir",
                    "unused", "scout-run"]) == 2
    assert "--allow-search" in capsys.readouterr().out


def test_scout_plan_cli_writes_artifacts(graph_db, tmp_path, capsys):
    rc = dc.main(["--db", graph_db, "--artifact-dir", str(tmp_path),
                  "scout-plan", "--max-queries", "8"])
    assert rc == 0
    plans = list(Path(tmp_path).rglob("scout-plan.json"))
    assert plans and len(json.loads(
        plans[0].read_text(encoding="utf-8"))["queries"]) <= 8


def test_set_relationship_operator_seam(graph_db):
    conn = _registry(graph_db)
    try:
        cid = cr.upsert_concept(conn, "example/some-repo", "repository")
        assert dc.main(["--db", graph_db, "set-relationship", cid,
                        "monitoring", "--reason", "operator test"]) == 0
        assert cr.get_concept(conn, cid)["user_relationship"] == "monitoring"
        # Mechanical promotion to durable_interest still requires the
        # operator method — the CLI default IS the operator seam.
        assert dc.main(["--db", graph_db, "set-relationship", cid,
                        "durable_interest", "--reason", "kept using it",
                        "--method", "operator"]) == 0
        assert cr.get_concept(conn, cid)[
            "user_relationship"] == "durable_interest"
    finally:
        conn.close()


def test_list_and_show(graph_db, capsys):
    conn = _registry(graph_db)
    try:
        cr.upsert_concept(conn, "example/alpha", "repository")
        conn.commit()
    finally:
        conn.close()
    assert dc.main(["--db", graph_db, "list"]) == 0
    out = capsys.readouterr().out
    assert "example/alpha" in out
    ref = cr.concept_identity_id("repository", "example/alpha")
    assert dc.main(["--db", graph_db, "show", ref]) == 0
    assert "example/alpha" in capsys.readouterr().out
