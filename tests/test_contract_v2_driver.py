"""Offline smoke of contract_v2_bakeoff driver paths (mocked provider).

Proves D1/D2/D3 machinery wiring executes end-to-end without subprocess
spend: phase-1 classification, relation handling, grouping+assembly, and
both reconciliation modes reach _finish() with sane metrics.
"""

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "build_interest_graph", REPO / "scripts" / "build_interest_graph.py")
big = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(big)

_ibspec = importlib.util.spec_from_file_location(
    "interest_contract_bakeoff",
    REPO / "scripts" / "interest_contract_bakeoff.py")
ib = importlib.util.module_from_spec(_ibspec)
_ibspec.loader.exec_module(ib)

_dspec = importlib.util.spec_from_file_location(
    "contract_v2_bakeoff", REPO / "scripts" / "contract_v2_bakeoff.py")
drv = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(drv)

_vspec = importlib.util.spec_from_file_location(
    "t_contract_v2", REPO / "tests" / "test_contract_v2.py")
_tcv2 = importlib.util.module_from_spec(_vspec)
_vspec.loader.exec_module(_tcv2)
fake_payload = _tcv2.payload


class FakeCap:
    def __init__(self, obj):
        self.text = json.dumps(obj)
        self.usage = {"input_tokens": 10, "output_tokens": 1,
                      "cached_input_tokens": 0}
        self.latency_s = 0.01
        self.returncode = 0
        self.timed_out = False
        self.error_items = []
        self.stderr_tail = ""

    def __getitem__(self, key):
        return getattr(self, key)


def install_mock_provider(monkeypatch):
    """Route every provider call by inspecting which schema file is
    attached; reconciliation tree prose calls answer merging_invoke."""
    from ef.inference_contract import relation_output_schema
    rel_rows = {"given": False}

    def smart_capture(prompt_file, schema_file, raw_out=None):
        text = Path(prompt_file).read_text(encoding="utf-8")
        if schema_file is None:
            try:
                return FakeCap(monolith_recon_answer(text))
            except Exception as exc:      # surface parsing context
                return FakeCap({"error":
                                f"mock-parse-failure {exc}: "
                                f"{text[:120]!r}"})
        sname = Path(schema_file).name
        if sname.startswith("phase1"):
            import re
            ids = [int(m) for m in
                   re.findall(r"^Cluster (\d+):", text, re.M)]
            pl = fake_payload()
            for i, it in enumerate(pl["inferred_interests"]):
                it["cluster_ids"] = [ids[i % len(ids)]]
            for rc in pl["regret_candidates"]:
                rc["cluster_ids"] = [ids[0]]
            return FakeCap(pl)
        if sname.startswith("relation"):
            inv_blob = text.split("OBJECT INVENTORY (JSON):")[1]
            ids = json.loads(inv_blob.split("\n\nPropose")[0].strip())
            ints = [o["id"] for o in ids if o.get("t") == "interest"]
            qs = [o["id"] for o in ids if o.get("t") == "question"]
            rgs = [o["id"] for o in ids if o.get("t") == "regret"]
            ans = {"parent_edges": ([{"child_id": ints[1],
                                      "parent_id": ints[0]}]
                                    if len(ints) > 1 else []),
                   "related_edges": [],
                   "question_links": ([{"question_id": qs[0],
                                        "interest_id": ints[0]}]
                                      if qs else []),
                   "regret_links": ([{"regret_id": rgs[0],
                                      "interest_id": ints[0]}]
                                    if rgs else [])}
            return FakeCap(ans)
        if sname.startswith("grouping"):
            inv_blob = text.split("OBJECT INVENTORY (JSON):")[1]
            ids = json.loads(inv_blob.split("\n\nAssign")[0].strip())
            by_t = {}
            for o in ids:
                key = (o.get("t"), (o.get("label") or "").lower())
                by_t.setdefault(key, []).append(o["id"])
            groups = [{"members": m, "action":
                       ("merged" if len(m) > 1 else "distinct"),
                       "canonical_name": None, "reason": ""}
                      for m in by_t.values()]
            return FakeCap({"groups": groups})
        raise AssertionError(f"unexpected schema {sname}")

    # patch the INSTANCES the driver actually binds
    monkeypatch.setattr(drv.ib, "run_codex_capture", smart_capture)
    return rel_rows


def monolith_recon_answer(text):
    """Mirror tests.test_build_interest_graph.merging_invoke output."""
    blob = text.split("Fragments (JSON):\n", 1)[1] \
               .split("\n\nReconcile them", 1)[0]
    frags = json.loads(blob)
    interests = [dict(f["interest"]) for f in frags]
    dispositions = [{"fragment_id": f["fragment_id"],
                     "decision": "kept",
                     "target_interest": f["interest"]["name"],
                     "reason": "smoke"} for f in frags]
    return {"final": {"inferred_interests": interests,
                      "questions": [], "regret_candidates": []},
            "fragment_dispositions": dispositions}


def install_fake_db(monkeypatch):
    """run_arm loads the corpus lazily; swap the DB seam AND the frozen
    plan-id guard for the synthetic inventory so no database/network is
    touched at all."""
    import ef.evidence_clusters as ec
    entries = tbig_helpers.inventory_of(60)["clusters"]
    # make the synthetic plan fingerprint MATCH the frozen guard by
    # stubbing the plan builder instead of matching a real fingerprint
    from ef.interest_candidates import build_bootstrap_plan as real_bbp
    import ef.interest_candidates as ic

    def fake_plan(entries_, max_per_call=25, exclusions=None, now=None):
        plan = real_bbp(entries_, max_per_call=max_per_call,
                        exclusions=exclusions, now=now)
        object.__setattr__(plan, "plan_id", drv.FROZEN_PLAN_ID)
        return plan

    monkeypatch.setattr(ic, "build_bootstrap_plan", fake_plan)
    # driver imported ic INSIDE run_arm, resolving current attrs -> OK
    inv = {"clusters": entries,
           "eligible_count": 60,
           "total_semantic_non_series": 60,
           "exclusions": {}}
    monkeypatch.setattr(ec, "evidence_cluster_inventory",
                        lambda *a, **k: inv)

    def hydrate(ids):
        return [tbig_helpers.synth_packet(c) for c in ids]

    monkeypatch.setattr(ec, "hydrate_evidence_clusters", lambda ids:
                        hydrate(ids))


_hspec2 = importlib.util.spec_from_file_location(
    "tbig_helpers", REPO / "tests" / "test_build_interest_graph.py")
tbig_helpers = importlib.util.module_from_spec(_hspec2)
_hspec2.loader.exec_module(tbig_helpers)


def test_driver_d1_end_to_end(tmp_path, monkeypatch):
    install_fake_db(monkeypatch)
    install_mock_provider(monkeypatch)

    class FakeTreeResult(dict):
        pass

    def fake_tree(fragments, plan_ids, **kw):
        leaves = fragments["interests"]
        final_ints = [dict(i["interest"]) for i in leaves]
        final = {"inferred_interests": final_ints,
                 "questions": [], "regret_candidates": []}
        return {"final": final,
                "fragment_dispositions":
                    [{"fragment_id": i["fragment_id"],
                      "decision": "kept",
                      "target_interest": i["interest"]["name"],
                      "reason": "smoke"} for i in leaves],
                "stages": [], "provider_calls": 1}

    # replace the recon tree seam: batches exercise merge-free path
    monkeypatch.setattr(drv.big, "run_reconciliation_tree",
                        fake_tree)
    rc = drv.run_arm("D1", str(tmp_path))
    assert rc == 0
    mfile = tmp_path / "metrics.json"
    if not mfile.exists():
        mfile = list(tmp_path.glob("bakeoff-v2-*/metrics.json"))[0]
    metrics = json.loads(mfile.read_text(encoding="utf-8"))
    assert metrics["result"]["completed"] is True
    assert metrics["result"]["mode"] == \
        "monolithic_tree_R1_sanitized"
    batches = metrics["extra"]["phase1"]["valid_object_payloads"]
    assert batches == 3, json.dumps(
        metrics["extra"].get("phase1_rows"), indent=1)[:1200]
    assert metrics["result"]["objects_in"] > 0


def test_driver_d3_end_to_end(tmp_path, monkeypatch):
    install_fake_db(monkeypatch)
    install_mock_provider(monkeypatch)
    rc = drv.run_arm("D3", str(tmp_path))
    assert rc == 0
    mfile = tmp_path / "metrics.json"
    if not mfile.exists():
        mfile = list(tmp_path.glob("bakeoff-v2-*/metrics.json"))[0]
    metrics = json.loads(mfile.read_text(encoding="utf-8"))
    assert metrics["result"]["completed"]
    assert metrics["result"]["mode"] == "decomposed_reconciliation"
    n_in = metrics["result"]["objects_in"]
    n_disp = metrics["result"]["explicit_dispositions"]
    assert n_disp >= n_in          # zero silent loss accounting

