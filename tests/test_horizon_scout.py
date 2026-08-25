"""Tests for ef.horizon_scout: category-query planning (zero network),
spend-gated execution over a mocked transport seam, GitHub identity
normalization, registry ingestion, and novelty triage. All names are
fictional; no test touches the network."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from ef import concept_registry as cr
from ef import horizon_scout as hs


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _make_graph_db(path, *, shuffle_seed=None):
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
    rows = [
        ("INSERT INTO interests VALUES (?,?,?,?,?)",
         ("i1", "distributed computing", "domain", "durable", "learning")),
        ("INSERT INTO interests VALUES (?,?,?,?,?)",
         ("i2", "distributed computing", "domain", "active", "learning")),
        ("INSERT INTO interests VALUES (?,?,?,?,?)",
         ("i3", "old faded thing", "topic", "dormant", "entertainment")),
        ("INSERT INTO goals VALUES (?,?,?)",
         ("g1", "reduce coordination overhead in replicated logs", "open")),
        ("INSERT INTO information_needs VALUES (?,?,?,?,?)",
         ("n1", "find lighter consensus protocols for edge clusters", "i1", "g1", "open")),
    ]
    if shuffle_seed is not None:
        import random
        random.Random(shuffle_seed).shuffle(rows)
    for sql, params in rows:
        conn.execute(sql, params)
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture()
def graph_db(tmp_path):
    return _make_graph_db(tmp_path / "graph.sqlite")


@pytest.fixture()
def registry(tmp_path):
    conn = cr.connect(db_path=tmp_path / "registry.sqlite")
    yield conn
    conn.close()


def _fake_seam(results_by_query=None, default=None):
    calls = []

    def seam(tool, arguments):
        calls.append((tool, dict(arguments)))
        if results_by_query is not None:
            for q, recs in results_by_query.items():
                if q in arguments.get("search_query", ""):
                    return list(recs)
        return list(default or [])

    seam.calls = calls
    return seam


# ---------------------------------------------------------------------------
# 1. PLAN
# ---------------------------------------------------------------------------


def test_plan_queries_are_categories_no_product_names(graph_db):
    plan = hs.build_scout_plan(graph_db)
    assert plan.queries
    assert plan.policy_version == hs.SCOUT_POLICY_VERSION
    for q in plan.queries:
        assert isinstance(q, hs.ScoutQuery)
        assert q.policy_version == hs.SCOUT_POLICY_VERSION
        # category shape: template-derived, no specific product name
        assert "{domain}" not in q.query
        lowered = q.query.casefold()
        assert "nebulamesh" not in lowered  # fictional product must not appear


def test_plan_dedups_same_domain(graph_db):
    plan = hs.build_scout_plan(graph_db)
    texts = [q.query for q in plan.queries]
    assert len(texts) == len(set(t.casefold() for t in texts))
    # i1 and i2 share domain "distributed computing" -> queries identical
    # modulo template; dedup guarantees uniqueness above.


def test_plan_has_adjacent_and_wildcard(graph_db):
    plan = hs.build_scout_plan(graph_db, max_queries=12)
    explorations = {q.exploration for q in plan.queries}
    assert "adjacent" in explorations
    assert "wildcard" in explorations
    assert "known_domain" in explorations


def test_plan_deterministic_same_seed_same_plan_id(tmp_path):
    db1 = _make_graph_db(tmp_path / "g1.sqlite")
    db2 = _make_graph_db(tmp_path / "g2.sqlite")
    p1 = hs.build_scout_plan(db1, now="2026-08-24T00:00:00Z")
    p2 = hs.build_scout_plan(db2, now="2026-08-24T09:00:00Z")
    assert p1.plan_id == p2.plan_id
    assert [q.query for q in p1.queries] == [q.query for q in p2.queries]
    assert p1.created_at != p2.created_at  # timestamp not part of identity


def test_plan_deterministic_under_shuffled_insertion(tmp_path):
    db1 = _make_graph_db(tmp_path / "a.sqlite")
    db2 = _make_graph_db(tmp_path / "b.sqlite", shuffle_seed=7)
    p1 = hs.build_scout_plan(db1)
    p2 = hs.build_scout_plan(db2)
    assert p1.plan_id == p2.plan_id
    assert [q.query_id for q in p1.queries] == [q.query_id for q in p2.queries]


def test_plan_makes_no_network_calls(graph_db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("planning opened a socket")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    plan = hs.build_scout_plan(graph_db)
    assert plan.queries


def test_plan_from_empty_db_is_wildcards(tmp_path):
    plan = hs.build_scout_plan(str(tmp_path / "missing.sqlite"))
    assert plan.queries
    assert all(q.exploration == "wildcard" for q in plan.queries)
    assert {q.query for q in plan.queries} <= set(hs.WILDCARD_QUERIES)


def test_plan_to_dict_roundtrip(graph_db):
    plan = hs.build_scout_plan(graph_db)
    d = plan.to_dict()
    assert d["plan_id"] == plan.plan_id
    assert len(d["queries"]) == len(plan.queries)
    assert d["queries"][0]["query_id"] == plan.queries[0].query_id


# ---------------------------------------------------------------------------
# 2. BUDGET ENFORCEMENT
# ---------------------------------------------------------------------------


def _rich_graph_db(tmp_path):
    """4 distinct interest domains x 2 queries each fills known_domain=8."""
    conn = sqlite3.connect(str(tmp_path / "rich.sqlite"))
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
    for i, name in enumerate(
        ["distributed computing", "stream processing systems",
         "consensus protocols", "edge computing"], start=1
    ):
        conn.execute("INSERT INTO interests VALUES (?,?,?,?,?)",
                     (f"ri{i}", name, "domain", "durable", "learning"))
    conn.execute("INSERT INTO goals VALUES (?,?,?)",
                 ("rg1", "reduce coordination overhead in replicated logs", "open"))
    conn.execute("INSERT INTO information_needs VALUES (?,?,?,?,?)",
                 ("rn1", "find lighter consensus protocols for edge clusters", "ri1", "rg1", "open"))
    conn.commit()
    conn.close()
    return str(tmp_path / "rich.sqlite")


def test_budget_split_roughly_70_20_10(tmp_path):
    plan = hs.build_scout_plan(_rich_graph_db(tmp_path), max_queries=12)
    counts = {"known_domain": 0, "adjacent": 0, "wildcard": 0}
    for q in plan.queries:
        counts[q.exploration] += 1
    assert abs(counts["known_domain"] - 8) <= 1
    assert abs(counts["adjacent"] - 2) <= 1
    assert abs(counts["wildcard"] - 2) <= 1
    assert counts["adjacent"] >= 1 and counts["wildcard"] >= 1


def test_wildcards_come_from_fixed_generic_list(graph_db):
    plan = hs.build_scout_plan(graph_db, max_queries=12)
    wilds = [q.query for q in plan.queries if q.exploration == "wildcard"]
    assert wilds
    for w in wilds:
        assert w in hs.WILDCARD_QUERIES


def test_at_most_two_queries_per_origin(graph_db):
    plan = hs.build_scout_plan(graph_db, max_queries=12)
    per_origin: dict[str, int] = {}
    for q in plan.queries:
        if q.origin_id:
            per_origin[q.origin_id] = per_origin.get(q.origin_id, 0) + 1
    assert all(n <= 2 for n in per_origin.values())


# ---------------------------------------------------------------------------
# 3. MCP CLIENT SEQUENCE (transport monkeypatched)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body, session_id=None):
        self._body = body.encode("utf-8")
        self.headers = {}
        if session_id:
            self.headers["mcp-session-id"] = session_id

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _sse(payload):
    return "event: message\ndata: " + payload + "\n\n"


def _install_transport(monkeypatch, responses):
    """responses: list of (body, session_id). Captures Request objects."""
    requests = []

    def fake_urlopen(req, timeout=None):
        requests.append(req)
        body, sid = responses[len(requests) - 1]
        return _FakeResponse(body, sid)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return requests


def test_default_mcp_call_initialize_then_tools_call(monkeypatch):
    import json as _json
    init_body = _sse(_json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}}))
    result = {
        "content": [
            {"type": "text", "text": _json.dumps(
                {"results": [{"url": "https://github.com/acme/nebulamesh",
                              "title": "NebulaMesh", "snippet": "fictional"}]}
            )}
        ]
    }
    tool_body = _sse(_json.dumps({"jsonrpc": "2.0", "id": 2, "result": result}))
    reqs = _install_transport(monkeypatch, [(init_body, "sess-42"), (tool_body, "sess-42")])

    records = hs._default_mcp_call("query", {"search_query": "x", "num_results": 5, "tier": "fast"})

    assert len(reqs) == 2
    import json as j
    p1 = j.loads(reqs[0].data.decode())
    assert p1["method"] == "initialize"
    assert p1["params"]["protocolVersion"] == "2024-11-05"
    hdrs = {k.lower(): v for k, v in reqs[1].header_items()}
    assert hdrs["mcp-session-id"] == "sess-42"
    p2 = j.loads(reqs[1].data.decode())
    assert p2["method"] == "tools/call"
    assert p2["params"]["name"] == "query"
    assert records and records[0]["url"] == "https://github.com/acme/nebulamesh"


def test_default_mcp_call_iserror_raises(monkeypatch):
    import json as _json
    init_body = _sse(_json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}))
    tool_body = _sse(_json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"isError": True, "content": []}}
    ))
    _install_transport(monkeypatch, [(init_body, "s"), (tool_body, "s")])
    with pytest.raises(hs.ScoutUnavailable):
        hs._default_mcp_call("query", {"search_query": "x"})


def test_default_mcp_call_http_error_raises(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "down", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(hs.ScoutUnavailable):
        hs._default_mcp_call("query", {"search_query": "x"})


# ---------------------------------------------------------------------------
# 4. SPEND GATE
# ---------------------------------------------------------------------------


def test_run_scout_requires_allow_search(graph_db):
    plan = hs.build_scout_plan(graph_db, max_queries=6)
    with pytest.raises(PermissionError, match="--allow-search"):
        hs.run_scout(plan, mcp_call=_fake_seam(default=[]))


def test_run_scout_rejects_pro_tier(graph_db):
    plan = hs.build_scout_plan(graph_db, max_queries=6)
    with pytest.raises(ValueError):
        hs.run_scout(plan, mcp_call=_fake_seam(default=[]),
                     allow_search=True, tier="pro")


def test_run_scout_default_tier_is_fast(graph_db):
    plan = hs.build_scout_plan(graph_db, max_queries=6)
    seam = _fake_seam(default=[{"url": "https://example.com/x", "title": "t"}])
    hs.run_scout(plan, mcp_call=seam, allow_search=True)
    assert all(args.get("tier") == "fast" for _, args in seam.calls)


# ---------------------------------------------------------------------------
# 5. GITHUB NORMALIZATION
# ---------------------------------------------------------------------------


def test_github_normalization_identity_is_owner_repo():
    a = hs.normalize_github_repo("https://github.com/Example/NebulaMesh")
    b = hs.normalize_github_repo("https://github.com/example/nebulamesh/")
    c = hs.normalize_github_repo("https://www.github.com/Example/NebulaMesh.git")
    assert a == b == c
    assert a[0] == "example/nebulamesh"
    assert a[1] == "https://github.com/example/nebulamesh"


def test_github_normalization_deep_path_and_non_github():
    deep = hs.normalize_github_repo("https://github.com/o/r/tree/main")
    assert deep == ("o/r", "https://github.com/o/r")
    assert hs.normalize_github_repo("https://gitlab.com/o/r") is None
    assert hs.normalize_github_repo("https://github.com/onlyowner") is None
    assert hs.normalize_github_repo("not a url") is None


# ---------------------------------------------------------------------------
# 6. INGEST
# ---------------------------------------------------------------------------


def test_ingest_creates_deterministic_repo_concept_from_url(registry):
    results = [
        {"query_id": "q_1", "query": "new open source projects distributed computing",
         "backend": "brave", "title": "Fancy Display Name",
         "url": "https://github.com/Acme/NebulaMesh", "snippet": "fictional repo"},
    ]
    stats = hs.ingest_external_results(registry, results, run_id="run-1")
    assert stats["concepts"] == 1
    assert stats["observations"] == 1
    assert stats["skipped_non_github"] == 0
    row = registry.execute("SELECT * FROM concepts").fetchone()
    assert row["canonical_name"] == "acme/nebulamesh"  # URL-derived, not the title
    assert row["concept_type"] == "repository"
    assert row["lifecycle_state"] == "candidate"
    assert row["user_relationship"] == "unknown"
    # replay: same deterministic identity, idempotent observation
    hs.ingest_external_results(registry, results, run_id="run-2")
    assert registry.execute("SELECT COUNT(*) c FROM concepts").fetchone()["c"] == 1


def test_ingest_same_repo_across_queries_joins_concept(registry):
    results = [
        {"query_id": "q_1", "query": "cat one", "backend": "brave",
         "title": "NebulaMesh", "url": "https://github.com/acme/nebulamesh",
         "snippet": "s1"},
        {"query_id": "q_2", "query": "cat two", "backend": "exa",
         "title": "NebulaMesh again", "url": "https://github.com/Acme/NebulaMesh.git",
         "snippet": "s2"},
    ]
    stats = hs.ingest_external_results(registry, results, run_id="run-1")
    assert stats == {"concepts": 1, "observations": 2, "skipped_non_github": 0}
    assert registry.execute("SELECT COUNT(*) c FROM concepts").fetchone()["c"] == 1
    assert registry.execute("SELECT COUNT(*) c FROM concept_observations").fetchone()["c"] == 2
    source_ids = {r["source_id"] for r in
                  registry.execute("SELECT source_id FROM concept_observations").fetchall()}
    assert source_ids == {"q_1:brave", "q_2:exa"}


def test_ingest_skips_non_github_and_zero_results(registry):
    stats = hs.ingest_external_results(
        registry,
        [{"query_id": "q_1", "backend": "b", "title": "blog",
          "url": "https://blog.example.com/post", "snippet": "s"}],
        run_id="run-1",
    )
    assert stats == {"concepts": 0, "observations": 0, "skipped_non_github": 1}
    empty = hs.ingest_external_results(registry, [], run_id="run-1")
    assert empty == {"concepts": 0, "observations": 0, "skipped_non_github": 0}
    assert registry.execute("SELECT COUNT(*) c FROM concepts").fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# 7. NOVELTY
# ---------------------------------------------------------------------------


def test_novelty_new_to_corpus_true_on_no_mentions(registry):
    seam = _fake_seam(default=[{"title": "unrelated thing", "snippet": "no mention"}])
    out = hs.check_novelty(registry, "acme/nebulamesh", mcp_call=seam)
    assert out["new_to_registry"] is True
    assert out["new_to_corpus"] is True
    assert out["previously_known"] is False


def test_novelty_mention_means_not_new(registry):
    seam = _fake_seam(default=[{"title": "Acme/NeBulaMesh release", "snippet": ""}])
    out = hs.check_novelty(registry, "acme/nebulamesh", mcp_call=seam)
    assert out["new_to_corpus"] is False


def test_novelty_transport_failure_is_unknown(registry):
    def seam(tool, args):
        raise hs.ScoutUnavailable("down")
    out = hs.check_novelty(registry, "acme/nebulamesh", mcp_call=seam)
    assert out["new_to_corpus"] == "unknown"


def test_novelty_known_to_registry(registry):
    cid = cr.upsert_concept(registry, "acme/nebulamesh", "repository")
    cr.add_alias(registry, cid, "NebulaMesh")
    seam = _fake_seam(default=[])
    out = hs.check_novelty(registry, "acme/nebulamesh", mcp_call=seam)
    assert out["new_to_registry"] is False
    assert out["previously_known"] is True
    # alias resolution path too
    out2 = hs.check_novelty(registry, "NebulaMesh", mcp_call=seam)
    assert out2["new_to_registry"] is False


def test_novelty_no_seam_is_unknown(registry):
    out = hs.check_novelty(registry, "acme/nebulamesh")
    assert out["new_to_corpus"] == "unknown"


# ---------------------------------------------------------------------------
# 8. FAIL CLOSED
# ---------------------------------------------------------------------------


def _plan_with_queries():
    q1 = hs.ScoutQuery("q_aaa", "interest", "i1", "d", "emerging_projects",
                       "new open source projects d", "known_domain", hs.SCOUT_POLICY_VERSION)
    q2 = hs.ScoutQuery("q_bbb", "interest", "i2", "d", "new_methods",
                       "emerging d methods and techniques", "known_domain", hs.SCOUT_POLICY_VERSION)
    return hs.ScoutPlan("plan_x", "2026-08-24T00:00:00Z", hs.SCOUT_POLICY_VERSION, (q1, q2))


def _counts(conn):
    return {
        "concepts": conn.execute("SELECT COUNT(*) c FROM concepts").fetchone()["c"],
        "obs": conn.execute("SELECT COUNT(*) c FROM concept_observations").fetchone()["c"],
    }


def test_total_transport_failure_propagates_and_registry_unchanged(registry):
    def dead_seam(tool, args):
        raise hs.ScoutUnavailable("fleet down")

    with pytest.raises(hs.ScoutUnavailable):
        hs.run_scout(_plan_with_queries(), mcp_call=dead_seam, allow_search=True)
    # nothing fabricated into the registry
    before = _counts(registry)
    assert before == {"concepts": 0, "obs": 0}


def test_single_query_failure_fail_soft(graph_db):
    plan = hs.build_scout_plan(graph_db, max_queries=6)
    good = [{"url": "https://github.com/acme/nebulamesh", "title": "NebulaMesh",
             "snippet": "fictional", "backend": "brave"}]
    state = {"failed_once": False}

    def flaky(tool, args):
        if not state["failed_once"]:
            state["failed_once"] = True
            raise hs.ScoutUnavailable("one query transport failure")
        return good

    out = hs.run_scout(plan, mcp_call=flaky, allow_search=True)
    errors = [r for r in out if "error" in r]
    records = [r for r in out if "url" in r]
    assert len(errors) == 1
    assert errors[0]["query_id"]
    assert records  # other queries' results intact
    assert all(r.get("backend") and r.get("query_id") for r in records)
    # records without a URL never appear
    assert all("url" in r for r in out if "error" not in r)
