"""ENTITY_RESOLUTION_STRESS_DIAGNOSTIC - Arm A runner. NON_DECISION_DIAGNOSTIC.

Loads er_stress/fixture_er_stress.json into a FRESH Arm-A-style store (imported
from ../arm_a/store.py via sys.path), ingests the evidence stream, then resolves
each declared stress case using ONLY the store's existing exact-normalized alias
resolution (ef.concept_registry.resolve_alias). No mechanism is upgraded,
patched, or extended: the point is to measure the CURRENT mechanism.

Per case it records: input refs, mechanism used, candidate/normalized detail,
resolved ids, expected ground truth, and a PASS/PARTIAL/FAIL verdict.
Writes results_arm_a.json next to this script.

Verdict rules (same rubric as DIAGNOSTIC.md):
  MERGE_TO_ONE expected:
    PASS    every probe resolves to the expected canonical entity
    FAIL    any probe resolves to a WRONG entity, or zero probes resolve
    PARTIAL otherwise (mix of hits and unresolved probes)
  DISTINCT expected (positional targets):
    FAIL    any two probes with different targets collapse onto one resolved id,
            or any probe resolves to a wrong non-target entity
    PASS    every probe hits its own target and all resolved ids are distinct
    PARTIAL otherwise

Run: python run_arm_a.py   (Python 3.14, stdlib only)
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGE1 = HERE.parent
if str(STAGE1) not in sys.path:
    sys.path.insert(0, str(STAGE1))

from arm_a.store import ArmAStore  # noqa: E402
from ef import concept_registry as cr  # noqa: E402  (reused substrate, read-only use)

FIXTURE_PATH = HERE / "fixture_er_stress.json"
RESULTS_PATH = HERE / "results_arm_a.json"
MECHANISM = (
    "exact-normalized alias lookup: ef.concept_registry.resolve_alias on "
    "normalized_alias equality, ORDER BY concept_id LIMIT 1; no fuzzy match, "
    "no context/source parameter, no temporal validity, no acronym expansion"
)


def resolve_probe(store: ArmAStore, ref: str) -> dict:
    """Resolve one surface form through the store's EXISTING mechanism only.

    Adds read-only introspection of the reused registry tables so each verdict
    shows WHY the mechanism behaved as it did (candidates vs empty lookup).
    """
    normalized = cr.normalize_alias(ref)
    candidates = [
        r["concept_id"] for r in store.conn.execute(
            "SELECT concept_id FROM concept_aliases WHERE normalized_alias=?"
            " ORDER BY concept_id", (normalized,)).fetchall()
    ]
    resolved_entity = store.resolve_name(ref)  # REUSED exact-normalized path
    resolved_concept = cr.resolve_alias(store.conn, ref)
    return {
        "ref": ref,
        "normalized_ref": normalized,
        "candidate_concept_ids": candidates,
        "resolved_entity_id": resolved_entity,
        "resolved_concept_id": resolved_concept,
    }


def _entity_map_pairs(store: ArmAStore) -> list[dict]:
    return [dict(r) for r in store.conn.execute(
        "SELECT entity_id, concept_id FROM ea_entity_map ORDER BY entity_id")]


def score_case(case: dict, probes: list[dict]) -> dict:
    exp = case["expected"]
    relation = exp["relation"]

    if relation == "MERGE_TO_ONE":
        canon = exp["canonical_entity_id"]
        for p in probes:
            got = p["resolved_entity_id"]
            p["expected"] = {"relation": relation, "target": canon}
            if got == canon:
                p["verdict"] = "HIT_EXPECTED"
            elif got is None:
                p["verdict"] = "MISS_UNRESOLVED"
            else:
                p["verdict"] = "WRONG_ENTITY"
        hits = sum(1 for p in probes if p["verdict"] == "HIT_EXPECTED")
        wrong = any(p["verdict"] == "WRONG_ENTITY" for p in probes)
        if wrong or hits == 0:
            case_verdict = "FAIL"
        elif hits == len(probes):
            case_verdict = "PASS"
        else:
            case_verdict = "PARTIAL"
    else:  # DISTINCT, positional targets
        targets = exp["entity_ids"]
        for i, p in enumerate(probes):
            target = targets[i]
            got = p["resolved_entity_id"]
            p["expected"] = {"relation": relation, "target": target}
            if got == target:
                p["verdict"] = "HIT_EXPECTED"
            elif got is None:
                p["verdict"] = "MISS_UNRESOLVED"
            else:
                p["verdict"] = "WRONG_ENTITY"
        resolved_ids = [p["resolved_entity_id"] for p in probes]
        collisions = {
            rid for rid in resolved_ids
            if rid is not None and resolved_ids.count(rid) > 1
        }
        wrong = any(p["verdict"] == "WRONG_ENTITY" for p in probes)
        if collisions or wrong:
            case_verdict = "FAIL"
        elif all(p["verdict"] == "HIT_EXPECTED" for p in probes):
            case_verdict = "PASS"
        else:
            case_verdict = "PARTIAL"

    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "description": case["description"],
        "mechanism": MECHANISM,
        "inputs": case["inputs"],
        "probe_results": probes,
        "resolved_ids": [p["resolved_entity_id"] for p in probes],
        "expected": case["expected"],
        "verdict": case_verdict,
    }


def main() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    store = ArmAStore(None)  # FRESH isolated :memory: store, no file, no prod DB
    try:
        ingest_report = store.ingest_fixture(fixture)

        cases = []
        for case in fixture["er_cases"]:
            probes = [resolve_probe(store, inp["ref"]) for inp in case["inputs"]]
            scored = score_case(case, probes)
            # attach evidence-context hints to matching probe rows (by order)
            for scored_probe, inp in zip(scored["probe_results"], case["inputs"]):
                scored_probe["source_hint"] = inp.get("source_hint")
                scored_probe["context_hint"] = inp.get("context_hint")
            cases.append(scored)

        counts = {v: sum(1 for c in cases if c["verdict"] == v)
                  for v in ("PASS", "PARTIAL", "FAIL")}
        total = len(cases)
        score = round(
            (counts["PASS"] + 0.5 * counts["PARTIAL"]) / total, 3) if total else 0.0

        results = {
            "schema": "er-stress-results-v1",
            "label": "NON_DECISION_DIAGNOSTIC",
            "diagnostic_name": "ENTITY_RESOLUTION_STRESS_DIAGNOSTIC",
            "arm": "A",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "fixture_version": fixture["fixture_version"],
            "store": "ArmAStore(:memory:) fresh instance, ea_* + reused ef registry tables",
            "ingest_report": ingest_report,
            "mechanism": MECHANISM,
            "scorecard": {
                "cases_total": total,
                **counts,
                "weighted_score": score,
                "scoring": "PASS=1 PARTIAL=0.5 FAIL=0",
            },
            "entity_map_snapshot": _entity_map_pairs(store),
            "cases": cases,
            "decision_note": (
                "This artifact informs architectural interpretation only; per the "
                "Stage-1 preregistration it CANNOT change the X1..X14 decision."),
        }
        RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    finally:
        store.close()

    print(f"WROTE {RESULTS_PATH}")
    print(f"Arm A ER-stress scorecard: PASS={counts['PASS']} "
          f"PARTIAL={counts['PARTIAL']} FAIL={counts['FAIL']} "
          f"(weighted {score} of 1.0 over {total} cases)")
    for c in cases:
        print(f"  [{c['verdict']:7s}] {c['case_id']} {c['category']}: "
              f"resolved={c['resolved_ids']} expected={c['expected']}")


if __name__ == "__main__":
    main()
