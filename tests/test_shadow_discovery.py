"""Unit tests for the SHADOW discovery planner (pure planning; the
execution seam is injected/fake in every test — no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ef import shadow_discovery as sd


ANCHORS = ["agentic coding assistants", "evidence-driven experiments", "trading indicators"]


def test_plan_respects_budget_and_exploration():
    plan = sd.build_shadow_plan(ANCHORS, max_queries=24)
    assert len(plan.queries) <= 24
    explorations = {q.exploration for q in plan.queries}
    assert "known_domain" in explorations
    assert "adjacent" in explorations
    assert "wildcard" in explorations  # exploration > 0 invariant
    assert sd._MAX_QUERIES >= len(plan.queries)


def test_plan_deterministic():
    a = sd.build_shadow_plan(ANCHORS, now="2026-01-01T00:00:00Z")
    b = sd.build_shadow_plan(ANCHORS, now="2026-06-01T00:00:00Z")
    assert a.plan_id == b.plan_id  # identity is query-set only, not clock
    assert [q.query for q in a.queries] == [q.query for q in b.queries]


def test_all_operators_present():
    plan = sd.build_shadow_plan(ANCHORS, max_queries=24)
    ops = {q.operator for q in plan.queries}
    assert {"capability", "adjacency", "fingerprint"} <= ops
    # bridges or portability may be capped out at small budgets; require
    # at least adjacency + fingerprint variety
    assert any(q.operator == "adjacency" for q in plan.queries)


def test_adjacency_classes_rotate():
    qs = sd.adjacency_queries(ANCHORS, per_anchor=2)
    assert len(qs) == 6
    # classes distinct WITHIN each anchor (rotation avoids immediate repeats)
    for i in range(0, 6, 2):
        assert qs[i].meta["adjacency_class"] != qs[i + 1].meta["adjacency_class"]
    for q in qs:
        assert q.exploration == "adjacent"


def test_bridge_queries_pair_distinct_anchors():
    qs = sd.bridge_queries(ANCHORS, limit=8)
    assert qs
    for q in qs:
        a, b = q.meta["pair"]
        assert sd._norm(a) != sd._norm(b)


def test_no_meta_goal_as_query():
    # the personal-agency meta-goal must never appear as search text
    plan = sd.build_shadow_plan(ANCHORS + ["personal agency"], max_queries=24)
    for q in plan.queries:
        assert "personal agency" not in q.query


def test_run_shadow_uses_injected_seam_failsoft():
    plan = sd.build_shadow_plan(ANCHORS[:1], max_queries=8)

    def seam(tool, args):
        if "SKILL.md" in args["search_query"]:
            raise RuntimeError("transport down")
        return [{"url": "https://example.com/x", "title": "t", "snippet": "s"}]

    records = sd.run_shadow(plan, mcp_call=seam)
    ok = [r for r in records if "url" in r]
    errs = [r for r in records if "error" in r]
    assert ok and errs  # fail-soft: mixed success/failure, no fabrication


def test_fingerprint_matching():
    rec = {"title": "skill sync tool", "snippet": "converts SKILL.md across hosts",
           "url": "https://github.com/x/skill-sync/tree/main/skills/"}
    fps = sd.match_fingerprints(rec)
    assert "SKILL.md" in fps
    assert sd.portability_score(rec) >= 1


def test_disposition_not_popularity_based():
    famous = {"title": "100k stars famous repo", "snippet": "popular",
              "url": "https://github.com/a/famous"}
    assert sd.classify_disposition(famous) in ("WATCH", "IGNORE")
    portable = {"title": "multi-host skill converter", "snippet": "portable cross-host converter SKILL.md",
                "url": "https://github.com/a/conv"}
    assert sd.classify_disposition(portable) in ("TEST", "ADAPT")


def test_convergence_requires_independent_sources():
    records = [
        {"title": "skill portability converter SKILL.md", "snippet": "converts SKILL.md portable cross-host",
         "url": f"https://example{i}.com/x", "operator": "portability"}
        for i in range(3)
    ]
    findings = sd.detect_convergence(records)
    assert any(f["mechanism"] == "skill_portability" for f in findings)
    single = records[:1]
    assert sd.detect_convergence(single) == []


def test_shadow_report_shape():
    plan = sd.build_shadow_plan(ANCHORS[:1], max_queries=8)
    report = sd.shadow_report(plan, [])
    assert report["evaluation_contract"]["axes"]
    assert report["provenance"]["mode"] == "shadow"
    assert report["plan"]["policy_version"] == sd.SHADOW_POLICY_VERSION
