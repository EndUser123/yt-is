"""Arm B case runner: X1..X14 against Graphiti+FalkorDB.

Reads FALKORDB_URL (default redis://127.0.0.1:6379). If unreachable:
prints FALKORDB_UNAVAILABLE and exits 3.
"""

import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapter
from adapter import ArmB, parse_t

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixture.json")

results: dict[str, dict] = {}


def record(case: str, ok: bool, detail: str):
    results[case] = dict(ok=ok, detail=detail)
    print(f"{'PASS' if ok else 'FAIL'} {case}: {detail}")


async def wait_reachable(url: str, timeout_s: float = 3.0) -> Exception | None:
    try:
        g = adapter.build_graphiti(url)
        await asyncio.wait_for(g.driver.health_check(), timeout=timeout_s)
        return None
    except Exception as e:  # noqa: BLE001
        return e


async def main() -> int:
    url = os.environ.get("FALKORDB_URL", "redis://127.0.0.1:6379")
    err = await wait_reachable(url)
    if err is not None:
        print(f"FALKORDB_UNAVAILABLE ({url}): {type(err).__name__}: {err}")
        return 3

    arm = await adapter.load(FIXTURE, url)

    # X1 as-of 2026-01-15, E2 launch_year -> 2031 ASSERTED-ONLY
    claims = await arm.query_claim("E2", "launch_year", parse_t("2026-01-15T00:00:00Z"))
    ok = (
        len(claims) == 1
        and claims[0]["current_value"] == "2031"
        and claims[0]["status"] == "ASSERTED-ONLY"
        and claims[0]["provenance"] == ["EU03"]
    )
    record("X1", ok, json.dumps(claims))

    # X2 as-of 2026-01-19, E1 partners_with O1 -> SUPPORTED emerged 2026-01-18
    claims = await arm.query_claim("E1", "partners_with", parse_t("2026-01-19T00:00:00Z"))
    ok = (
        len(claims) == 1
        and claims[0]["status"] == "SUPPORTED"
        and str(claims[0]["emergence"]).startswith("2026-01-18")
        and sorted(claims[0]["provenance"]) == ["EU06", "EU08"]
    )
    record("X2", ok, json.dumps(claims))

    # X3 now, E2 launch_year -> 2033 SUPPORTED (EU03, EU09); 2031 historical
    claims = await arm.query_claim("E2", "launch_year", None)
    hist = await arm.query_claim("E2", "launch_year", parse_t("2026-01-19T00:00:00Z"))
    ok = (
        len(claims) == 1
        and claims[0]["current_value"] == "2033"
        and claims[0]["status"] == "SUPPORTED"
        and sorted(claims[0]["provenance"]) == ["EU03", "EU09"]
        and hist[0]["current_value"] == "2031"
    )
    record("X3", ok, json.dumps(dict(now=claims, asof_0119=hist)))

    # X4 now, E1 budget -> 2M and 5M coexist, each single-source
    claims = await arm.query_claim("E1", "budget", None)
    vals = sorted(c["current_value"] for c in claims)
    ok = (
        vals == ["2M", "5M"]
        and all(c["status"] == "ASSERTED-ONLY" for c in claims)
    )
    record("X4", ok, json.dumps(claims))

    # X5 alias resolution
    checks = {
        "the Alphard program": "E1",
        "ALPHARD initiative": "E1",
        "Alphard": "E1",
        "Alphard Minor": "E3",
    }
    ok = all((await arm.resolve_alias(k)) == v for k, v in checks.items())
    detail = {k: await arm.resolve_alias(k) for k in checks}
    record("X5", ok, json.dumps(detail))

    # X6 bridge T1-T2 via B1
    bridges = await arm.find_bridge("T1", "T2")
    ok = (
        len(bridges) == 1
        and bridges[0]["bridge"] == "B1"
        and bridges[0]["supporting_eus"] == ["EU10", "EU11"]
        and bridges[0]["supported"]
        and any(t.startswith("2026-01-24") for t in bridges[0]["evidence_ts"])
    )
    record("X6", ok, json.dumps(bridges))

    # X7 remove EU08 -> partners ASSERTED-ONLY; researches T1 still SUPPORTED
    arm7 = arm
    await arm7.remove_evidence("EU08")
    partners = await arm7.query_claim("E1", "partners_with", None)
    researches = await arm7.query_claim("E1", "researches", None)
    ok = (
        partners[0]["status"] == "ASSERTED-ONLY"
        and partners[0]["provenance"] == ["EU06"]
        and researches[0]["status"] == "SUPPORTED"
        and sorted(researches[0]["provenance"]) == ["EU01", "EU02"]
    )
    record("X7", ok, json.dumps(dict(partners=partners, researches=researches)))

    # X8 remove EU11 -> bridge gone; B1 enables T1 remains
    await arm7.remove_evidence("EU11")
    bridges = await arm7.find_bridge("T1", "T2")
    enables = await arm7.query_claim("B1", "enables", None)
    ok = (
        len(bridges) == 0
        and any(c["current_value"] == "T1" for c in enables)
    )
    record("X8", ok, json.dumps(dict(bridges=bridges, enables=enables)))

    # reload for later cases
    arm = await adapter.load(FIXTURE, url)

    # X9 now P1 leads E1 -> SUPPORTED emerged 2026-02-02
    claims = await arm.query_claim("P1", "leads", None)
    ok = (
        claims[0]["status"] == "SUPPORTED"
        and str(claims[0]["emergence"]).startswith("2026-02-02")
        and sorted(claims[0]["provenance"]) == ["EU12", "EU15"]
    )
    record("X9", ok, json.dumps(claims))

    # X10 as-of 2026-01-26 inclusive -> ASSERTED-ONLY, EU15 must not leak
    claims = await arm.query_claim("P1", "leads", parse_t("2026-01-26T00:00:00Z"))
    ok = (
        claims[0]["status"] == "ASSERTED-ONLY"
        and claims[0]["provenance"] == ["EU12"]
    )
    record("X10", ok, json.dumps(claims))

    # X11 replay checkpoints
    checkpoints = {
        "2026-01-05T00:00:00Z": ("E1", "researches", "SUPPORTED"),
        "2026-01-18T00:00:00Z": ("E1", "partners_with", "SUPPORTED"),
        "2026-01-20T00:00:00Z": ("E2", "launch_year", "2033"),
        "2026-02-02T00:00:00Z": ("P1", "leads", "SUPPORTED"),
    }
    ok = True
    detail = {}
    for T, (s, p, want) in checkpoints.items():
        c = await arm.query_claim(s, p, parse_t(T))
        got = c[0]["current_value"] if want[0].isdigit() else c[0]["status"]
        detail[T] = c
        if got != want:
            ok = False
        # no post-T leakage: no EU with t > T in provenance
        for eu in c[0]["provenance"]:
            if arm.eu_time.get(eu) and arm.eu_time[eu] > parse_t(T):
                ok = False
    record("X11", ok, json.dumps(detail))

    # X12 provenance exactness
    p1 = await arm.provenance_of_edge("E1", "partners_with")
    p2 = await arm.provenance_of_edge("E1", "researches")
    ok = p1 == {"EU06", "EU08"} and p2 == {"EU01", "EU02"}
    record("X12", ok, f"partners={sorted(p1)} researches={sorted(p2)}")

    # X13 why-surfaced
    why = await arm.why_surfaced("T1", "T2")
    ok = (
        why is not None
        and why["supporting_eus"] == ["EU10", "EU11"]
        and why["discovery_route"]
        and why["novelty_state"]
        and why["evidence_maturity"]["source_count"] == 2
        and why["bridge_reason"]
    )
    record("X13", ok, json.dumps(why))

    # X14 concurrency: stale write against gen N must fail
    gen = await arm.read_generation()
    async def add_eu16():
        fixture_eu16 = dict(
            eu_id="EU16", t="2026-02-03T00:00:00Z", source_id="S3",
            text="Late-breaking: Project Alphard budget reaffirmed at 5M.",
            asserts=[{"subject": "E1", "predicate": "budget", "object": "5M"}],
        )
        nodes = {}
        for eid in ("E1",):
            rec, _, _ = await arm.driver.execute_query(
                "MATCH (n:Entity {uuid: $u}) RETURN n.uuid AS uuid", u=f"ent-{eid}", routing_="r"
            )
            nodes[eid] = type("N", (), {"uuid": rec[0]["uuid"]})()
        await arm.ingest_eu(fixture_eu16, nodes)

    committed = await arm.commit_with_generation(gen, add_eu16)  # fresh gen -> commits
    gen2 = await arm.read_generation()
    stale = await arm.commit_with_generation(gen, add_eu16)  # old gen -> must fail
    ok = committed and (not stale) and gen2 == gen + 1
    record("X14", ok, f"committed={committed} stale_rejected={not stale} gen={gen}->{gen2} "
                      "(Graphiti/FalkorDB provide no transactions; CAS token is adapter code)")

    passed = sum(1 for r in results.values() if r["ok"])
    print(f"\nArm B: {passed}/{len(results)} cases PASS")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
