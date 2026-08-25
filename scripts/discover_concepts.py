"""Concept discovery CLI — open-world discovery operations.

Durable memory (the Concept Registry) is separate from durable attention
(lifecycle/user_relationship). Internal scans read existing Evidence
Fabric data; horizon scouting reuses the P search fleet over CATEGORY
queries so unknown names arrive from search evidence, never from
configuration.

Spend discipline: planning never touches the network; `scout-run` is the
only network command and requires --allow-search; the search tier is
fast/medium/deep only — pro/quota tiers are never sent silently.

Usage:
    python scripts/discover_concepts.py internal-scan [--as-of YYYY-MM-DD]
    python scripts/discover_concepts.py list [--lifecycle L] [--relationship R]
    python scripts/discover_concepts.py show <concept-or-alias>
    python scripts/discover_concepts.py trend <concept-id>
    python scripts/discover_concepts.py scout-plan [--max-queries N]
    python scripts/discover_concepts.py scout-run --allow-search [--tier fast]
    python scripts/discover_concepts.py ingest-external <json-file>
    python scripts/discover_concepts.py set-relationship <id> <state> --reason R
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ARTIFACT_ROOT = Path("P:/.data/yt-is/ef/concept-discovery")

# Mechanical recurrence promotion for external candidates (v1 policy,
# initial values, not optimal): independent = distinct query/backend
# provenance; absolute floor prevents 1->2 noise from emerging.
RECURRENCE_POLICY = {
    "policy_version": "external-recurrence-v1",
    "min_observations": 3,
    "min_distinct_sources": 2,
}


def _connect(db_path=None):
    from ef.concept_registry import connect
    return connect(db_path)


def _run_dir(artifact_dir=None, name=None) -> Path:
    root = Path(artifact_dir) if artifact_dir else ARTIFACT_ROOT
    d = root / (name or time.strftime("%Y%m%dT%H%M%S"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def cmd_internal_scan(a) -> int:
    from ef import concept_discovery
    conn = _connect(a.db)
    try:
        run_dir = _run_dir(a.artifact_dir)
        summary = concept_discovery.scan_internal(
            conn, catalog_path=a.catalog, as_of=a.as_of)
        _write_json(run_dir / "run.json", summary)
        _write_json(run_dir / "candidate-summary.json", {
            "candidates": [dict(r) for r in
                           _safe_list(conn, lifecycle="candidate")],
            "emerging": [dict(r) for r in
                         _safe_list(conn, lifecycle="emerging")],
        })
        _write_json(run_dir / "trend-summary.json", {
            "cooling": [dict(r) for r in
                        _safe_list(conn, lifecycle="cooling")],
            "dormant": [dict(r) for r in
                        _safe_list(conn, lifecycle="dormant")],
        })
        print(json.dumps(summary, indent=2))
        print(f"[artifacts] {run_dir}")
    finally:
        conn.close()
    return 0


def _safe_list(conn, lifecycle=None):
    from ef.concept_registry import list_concepts
    try:
        return list_concepts(conn, lifecycle=lifecycle, limit=50)
    except Exception:
        return []


def cmd_list(a) -> int:
    from ef.concept_registry import list_concepts
    conn = _connect(a.db)
    try:
        rows = list_concepts(conn, lifecycle=a.lifecycle,
                             user_relationship=a.relationship)
        for r in rows:
            print(f"{r['concept_id']:<24} {r['lifecycle_state']:<10} "
                  f"{r['user_relationship']:<15} {r['concept_type']:<14} "
                  f"{r['canonical_name']}")
        print(f"[{len(rows)} concepts]")
    finally:
        conn.close()
    return 0


def _resolve(conn, ref: str):
    from ef.concept_registry import get_concept, resolve_alias
    hit = get_concept(conn, ref)
    if hit:
        return hit
    cid = resolve_alias(conn, ref)
    return get_concept(conn, cid) if cid else None


def cmd_show(a) -> int:
    from ef.concept_registry import observation_counts
    conn = _connect(a.db)
    try:
        concept = _resolve(conn, a.ref)
        if not concept:
            print(f"[not found] {a.ref}")
            return 1
        out = dict(concept)
        out["observation_counts"] = observation_counts(conn,
                                                       concept["concept_id"])
        out["aliases"] = [r["alias"] for r in conn.execute(
            "SELECT alias FROM concept_aliases WHERE concept_id=?",
            (concept["concept_id"],))]
        out["episodes"] = [dict(r) for r in conn.execute(
            "SELECT * FROM trend_episodes WHERE concept_id=? "
            "ORDER BY started_at", (concept["concept_id"],))]
        out["interest_links"] = [dict(r) for r in conn.execute(
            "SELECT * FROM concept_interest_links WHERE concept_id=?",
            (concept["concept_id"],))]
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


def cmd_trend(a) -> int:
    conn = _connect(a.db)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM trend_episodes WHERE concept_id=? "
            "ORDER BY started_at", (a.concept_id,))]
        if not rows:
            print(f"[no episodes] {a.concept_id}")
            return 1
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


def cmd_scout_plan(a) -> int:
    from ef.horizon_scout import build_scout_plan
    run_dir = _run_dir(a.artifact_dir)
    plan = build_scout_plan(a.graph_db or a.db, max_queries=a.max_queries)
    _write_json(run_dir / "scout-plan.json", plan.to_dict())
    for q in plan.queries:
        print(f"{q.query_id}  [{q.exploration}/{q.intent}]  {q.query}")
    print(f"[plan] {plan.plan_id}: {len(plan.queries)} queries -> "
          f"{run_dir / 'scout-plan.json'}")
    return 0


def _link_origins(conn, plan, results) -> list:
    """Link discovered concepts to the interests whose category queries
    surfaced them (provenance method 'semantic': the association is the
    query-origin evidence, recorded explicitly in provenance_json)."""
    from ef import concept_registry
    from ef.horizon_scout import normalize_github_repo
    origin_by_qid = {q.query_id: q for q in plan.queries}
    links = []
    for r in results:
        q = origin_by_qid.get(r.get("query_id"))
        norm = normalize_github_repo(r.get("url") or "")
        if not (q and norm and q.origin_kind == "interest" and q.origin_id):
            continue
        cid = concept_registry.concept_identity_id("repository", norm[0])
        concept_registry.link_concept_interest(
            conn, cid, q.origin_id, method="semantic",
            provenance={"via": "scout_query_origin",
                        "query_id": q.query_id, "query": q.query})
        links.append({"concept_id": cid, "interest_id": q.origin_id,
                      "query_id": q.query_id})
    return links


def cmd_scout_run(a) -> int:
    from ef import concept_registry
    from ef.horizon_scout import build_scout_plan, ingest_external_results, \
        run_scout
    if not a.allow_search:
        print("scout-run requires --allow-search (network spend gate)")
        return 2
    conn = _connect(a.db)
    try:
        plan = build_scout_plan(a.graph_db or a.db,
                                max_queries=a.max_queries)
        results = run_scout(plan, allow_search=True, tier=a.tier,
                            num_results=a.num_results)
        run_dir = _run_dir(a.artifact_dir)
        _write_json(run_dir / "scout-plan.json", plan.to_dict())
        _write_json(run_dir / "scout-results.json", results)
        run_id = f"scout_{time.strftime('%Y%m%dT%H%M%S')}"
        ingest = ingest_external_results(
            conn, [r for r in results if "url" in r], run_id=run_id)
        links = _link_origins(conn, plan, [r for r in results if "url" in r])
        promotions = promote_on_recurrence(conn)
        _write_json(run_dir / "promotion-decisions.json",
                    {"promotions": promotions, "origin_links": links})
        print(json.dumps({"ingest": ingest,
                          "origin_links": len(links),
                          "promotions": promotions["promoted"]}, indent=2))
        print(f"[artifacts] {run_dir}")
    finally:
        conn.close()
    return 0


def promote_on_recurrence(conn, as_of=None) -> dict:
    """Mechanical external-candidate promotion on observation recurrence.

    v1 initial policy: candidate -> emerging requires the absolute
    observation floor AND >= 2 distinct query/backend provenance sources.
    Uses only registry primitives; never invents evidence; the concept's
    user_relationship is untouched (world attention != user interest).
    """
    from ef.concept_registry import set_lifecycle
    decisions = []
    rows = conn.execute(
        "SELECT concept_id, canonical_name, lifecycle_state FROM concepts "
        "WHERE lifecycle_state = 'candidate'").fetchall()
    for row in rows:
        obs = conn.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT source_id) sources "
            "FROM concept_observations WHERE concept_id = ?",
            (row["concept_id"],)).fetchone()
        promote = (obs["n"] >= RECURRENCE_POLICY["min_observations"]
                   and obs["sources"] >=
                   RECURRENCE_POLICY["min_distinct_sources"])
        if promote:
            set_lifecycle(conn, row["concept_id"], "emerging",
                          reason=(f"external recurrence: {obs['n']} "
                                  f"observations across {obs['sources']} "
                                  f"independent sources "
                                  f"({RECURRENCE_POLICY['policy_version']})"))
        decisions.append({"concept_id": row["concept_id"],
                          "name": row["canonical_name"], "promoted": promote})
    return {"policy": RECURRENCE_POLICY, "promoted":
            [d for d in decisions if d["promoted"]], "decisions": decisions}


def cmd_ingest_external(a) -> int:
    from ef.horizon_scout import ingest_external_results
    payload = json.loads(Path(a.json_file).read_text(encoding="utf-8"))
    results = payload.get("results", payload if isinstance(payload, list)
                          else [])
    conn = _connect(a.db)
    try:
        summary = ingest_external_results(
            conn, [r for r in results if "url" in r],
            run_id=f"ingest_{time.strftime('%Y%m%dT%H%M%S')}")
        print(json.dumps(summary, indent=2))
    finally:
        conn.close()
    return 0


def cmd_set_relationship(a) -> int:
    from ef.concept_registry import RegistryError, set_user_relationship
    conn = _connect(a.db)
    try:
        concept = _resolve(conn, a.ref)
        if not concept:
            print(f"[not found] {a.ref}")
            return 1
        try:
            set_user_relationship(conn, a.ref if concept is None
                                  else concept["concept_id"], a.state,
                                  reason=a.reason or "operator CLI",
                                  method=a.method)
        except RegistryError as exc:
            print(f"[rejected] {exc}")
            return 2
        print(f"[ok] {concept['canonical_name']} -> {a.state}")
    finally:
        conn.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None,
                    help="registry DB override (tests)")
    ap.add_argument("--graph-db", default=None,
                    help="interest-graph DB for scout planning (tests)")
    ap.add_argument("--catalog", default=None,
                    help="EF catalog override for internal scans (tests)")
    ap.add_argument("--artifact-dir", default=None,
                    help="runtime artifact root override (tests)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("internal-scan")
    p.add_argument("--as-of", default=None)
    p.set_defaults(fn=cmd_internal_scan)

    p = sub.add_parser("list")
    p.add_argument("--lifecycle", default=None)
    p.add_argument("--relationship", default=None)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show")
    p.add_argument("ref")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("trend")
    p.add_argument("concept_id")
    p.set_defaults(fn=cmd_trend)

    p = sub.add_parser("scout-plan")
    p.add_argument("--max-queries", type=int, default=12)
    p.set_defaults(fn=cmd_scout_plan)

    p = sub.add_parser("scout-run")
    p.add_argument("--allow-search", action="store_true")
    p.add_argument("--tier", default="fast",
                   choices=["fast", "medium", "deep"])
    p.add_argument("--num-results", type=int, default=8)
    p.add_argument("--max-queries", type=int, default=12)
    p.set_defaults(fn=cmd_scout_run)

    p = sub.add_parser("ingest-external")
    p.add_argument("json_file")
    p.set_defaults(fn=cmd_ingest_external)

    p = sub.add_parser("set-relationship")
    p.add_argument("ref")
    p.add_argument("state")
    p.add_argument("--reason", default=None)
    p.add_argument("--method", default="operator")
    p.set_defaults(fn=cmd_set_relationship)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
