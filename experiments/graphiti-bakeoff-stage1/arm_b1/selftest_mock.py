"""Mock self-test for the B1 evaluator: agent: zcode

Runs X1..X14 against an in-memory store that mimics what Graphiti's real
pipeline is EXPECTED to produce from the fixture (resolved canonical node
names, facts carrying literals in text, episode backlinks, graphiti-written
invalid_at on the superseded launch_year edge). Purpose:
  1. runtime-verify the evaluator machinery end-to-end without a FalkorDB
     endpoint (all cases execute; read-only cases must PASS on a faithful mock),
  2. provide a repeatable regression harness for evaluator changes.

X14 stays UNTESTABLE here by design (the concurrency probe needs two REAL
connections and records its static isolation finding).

Usage: .venv2/Scripts/python.exe selftest_mock.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate as ev  # noqa: E402
from ingest import load_fixture, ordered_eus, parse_t  # noqa: E402

norm_name = ev.norm_name


class FakeDriver:
    """In-memory stand-in for FalkorDriver.execute_query covering exactly the
    queries evaluate.load_snapshot / bridge_paths_cypher issue."""

    def __init__(self):
        self.entities: dict[str, dict] = {}
        self.episodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    async def execute_query(self, q: str, **kw):
        if "count(e) AS n" in q:
            return [{"n": len(self.episodes)}], None, None
        if q.strip().startswith("MATCH (n:Entity"):
            return [
                {"uuid": u, "name": v.get("name"), "summary": v.get("summary", "")}
                for u, v in self.entities.items()
            ], None, None
        if q.strip().startswith("MATCH (e:Episodic"):
            return [dict(v, uuid=u) for u, v in self.episodes.items()], None, None
        if "left_edge_uuids" in q:
            return self._bridge(kw["ua"], kw["ub"]), None, None
        return [dict(e) for e in self.edges], None, None

    def _neighbors(self, uid: str) -> list[tuple[str, dict]]:
        out = []
        for e in self.edges:
            if e["src_uuid"] == uid:
                out.append((e["tgt_uuid"], e))
            elif e["tgt_uuid"] == uid:
                out.append((e["src_uuid"], e))
        return out

    def _bridge(self, ua: str, ub: str) -> list[dict]:
        rows = []
        for m_uuid, m_data in self.entities.items():
            left = self._neighbors(ua)
            right = self._neighbors(ub)
            left_via = [e for n, e in left if n == m_uuid]
            right_via = [e for n, e in right if n == m_uuid]
            if m_uuid not in (ua, ub) and left_via and right_via:
                rows.append({
                    "bridge_uuid": m_uuid,
                    "bridge_name": m_data.get("name"),
                    "left_edge_uuids": sorted({e["uuid"] for e in left_via}),
                    "right_edge_uuids": sorted({e["uuid"] for e in right_via}),
                })
        return rows


class MockGraphiti:
    """remove_episode with graphiti's first-author deletion rule."""

    def __init__(self, driver: FakeDriver):
        self.driver = driver

    async def remove_episode(self, ep_uuid: str):
        d = self.driver
        ep = d.episodes.pop(ep_uuid, None)
        if ep is None:
            return
        keep = []
        for e in d.edges:
            eps = [u for u in e.get("episodes") or []]
            if eps and eps[0] == ep_uuid:
                continue  # graphiti deletes edges whose FIRST backlink is gone
            e["episodes"] = [u for u in eps if u != ep_uuid]
            keep.append(e)
        d.edges[:] = keep


def build_mock_store(fixture: dict) -> tuple[FakeDriver, MockGraphiti]:
    d = FakeDriver()
    fact_text = {
        ("EU01", "researches"): "Project Alphard researches fusion propulsion.",
        ("EU01", "housed_at"): "The Alphard program at Helion Labs.",
        ("EU02", "researches"): "Helion Labs confirmed the ALPHARD initiative "
                                "studies fusion propulsion.",
        ("EU03", "launch_year"): "Project Betelgeuse launch year 2031.",
        ("EU04", "employed_by"): "M. Chen joined Helion Labs.",
        ("EU05", "budget"): "Project Alphard budget 2 million dollars.",
        ("EU06", "partners_with"): "Alphard partners with Helion Labs.",
        ("EU07", "budget"): "Documents show Project Alphard budget 5 million dollars.",
        ("EU08", "partners_with"): "Helion Labs announced partnership with "
                                   "the Alphard program.",
        ("EU09", "launch_year"): "The Betelgeuse project launch moved to 2033.",
        ("EU10", "enables"): "Cryogenic cooling advances enable fusion propulsion "
                             "reactors.",
        ("EU11", "enables"): "Cryogenic cooling is critical to spacecraft thermal "
                             "management.",
        ("EU12", "leads"): "Dr. Mira Chen appointed lead of Project Alphard.",
        ("EU15", "leads"): "Mira Chen presented Alphard results at the Helion seminar.",
    }
    eu_seen: set[str] = set()
    supersede_t: dict[str, datetime] = {}
    for ent in fixture["entities"]:
        d.entities[f"mock:{ent['entity_id']}"] = {"name": ent["canonical_name"]}
    for i, eu in enumerate(ordered_eus(fixture)):
        uid = f"ep:{eu['eu_id']}"
        eu_seen.add(eu["eu_id"])
        d.episodes[uid] = {
            "name": f"{eu['eu_id']} ({eu['source_id']})",
            "content": eu["text"],
            "source_description": next(s["channel"] for s in fixture["sources"]
                                       if s["source_id"] == eu["source_id"]),
            "valid_at": parse_t(eu["t"]),
            "entity_edges": [],
        }
        t_val = parse_t(eu["t"])
        # fixture encodes a supersession marker as a sibling object like
        # {"supersedes_value": "2031"} on the same evidence unit
        supersedes_vals = [
            m["supersedes_value"] for m in (eu.get("asserts") or [])
            if isinstance(m, dict) and m.get("supersedes_value")
        ]
        for a in (eu.get("asserts") or []):
            if not isinstance(a, dict) or "predicate" not in a or "subject" not in a:
                continue
            key = (eu["eu_id"], a["predicate"])
            pred = a["predicate"]
            subj = f"mock:{a['subject']}"
            # object: entity ref OR literal folded into subject->value edge text
            tgt_id = f"mock:{a['object']}"
            literal_objects = {"2031", "2033", "2M", "5M"}
            if a["object"] in literal_objects:
                tgt = subj  # literal stays inside fact text only
            else:
                tgt = tgt_id if f"mock:{a['object']}" in d.entities else subj
            for sv in supersedes_vals:
                supersede_t[f"{a['subject']}|{pred}|{sv}"] = t_val
            euid = f"e:{eu['eu_id']}:{pred}"
            existing = next((x for x in d.edges if x["uuid"] == euid), None)
            fact = fact_text.get(key, f"{fixture['entities'][0]['canonical_name']} {pred}")
            if existing is not None:
                existing["episodes"].append(uid)
                continue
            d.edges.append({
                "uuid": euid, "fact": fact,
                "episodes": [uid],
                "valid_at": t_val, "invalid_at": None, "expired_at": None,
                "created_at": t_val, "src_uuid": subj, "tgt_uuid": tgt,
            })
    # Post-pass: real Graphiti writes invalidation onto the SUPERSEDED edge when
    # the replacing evidence arrives. Apply each (subject|predicate|oldvalue ->
    # t_supersede) marker to the old-value edge here.
    for key_s, t_sup in supersede_t.items():
        subj_id, _pred, old_val = key_s.split("|")
        for e in d.edges:
            if {e.get("src_uuid"), e.get("tgt_uuid")} & {f"mock:{subj_id}"} \
                    and old_val in norm_name(e.get("fact") or ""):
                e["invalid_at"] = t_sup
    g = MockGraphiti(d)
    return d, g


async def main() -> int:
    fixture = load_fixture()
    driver, graphiti = build_mock_store(fixture)
    snap0 = await ev.load_snapshot(driver, "b1_mock")
    eva = ev.B1Evaluator(graphiti, driver, fixture, "b1_mock")

    cases = []
    x6_holder: dict[str, dict] = {}

    async def do_x6():
        r = await eva.x6_bridge(snap0)
        x6_holder["r"] = r
        return r

    seq = [
        ("X1", lambda: eva.x1_launch_asof(snap0)),
        ("X2", lambda: eva.x2_partners_asof(snap0)),
        ("X3", lambda: eva.x3_launch_current(snap0)),
        ("X4", lambda: eva.x4_budget_coexist(snap0)),
        ("X5", lambda: eva.x5_identity(snap0)),
        ("X6", do_x6),
        ("X9", lambda: eva.x9_leads_now(snap0)),
        ("X10", lambda: eva.x10_leads_asof_inclusive(snap0)),
        ("X11", lambda: eva.x11_replay_checkpoints(snap0)),
        ("X12", lambda: eva.x12_provenance(snap0)),
        ("X13", lambda: eva.x13_why_surfaced(x6_holder.get("r", {}))),
        ("X7", lambda: eva.x7_remove_eu08(snap0)),
        ("X8", lambda: eva.x8_remove_eu11()),
        ("X14", lambda: eva.x14_concurrency()),
    ]
    expected_untestable = {"X14"}  # concurrency probe needs two real connections
    failures = []
    for cid, fn in seq:
        try:
            res = await fn()
        except Exception as e:  # noqa: BLE001
            import traceback

            res = {"case": cid, "status": "ERROR",
                   "error": f"{type(e).__name__}: {e}",
                   "trace": traceback.format_exc()[-1500:]}
        print(f"{cid:4s} {res['status']:10s} {str(res.get('failure_class') or '')}")
        ok_expected = (
            res["status"] == "UNTESTABLE" if cid in expected_untestable
            else res["status"] == "PASS"
        )
        if not ok_expected:
            failures.append((cid, res))
        cases.append(res)

    print()
    if failures:
        print("MOCK SELFTEST FAILURES:")
        for cid, res in failures:
            print("---", cid)
            print(res.get("trace") or res.get("actual"))
        return 1
    print("MOCK SELFTEST OK: evaluator executes end-to-end; all read-only cases "
          "PASS on a faithful mock graph; X14 records untested-by-design.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
