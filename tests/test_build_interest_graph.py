"""Tests for scripts/build_interest_graph.py — v2 contract fidelity.

Offline-safe: provider subprocess calls are mocked; clusters are
synthetic; result/prompt artifacts go to tmp_path. Synthetic fictional
topics only (distributed databases, compiler optimization, gardening,
astronomy).
"""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "build_interest_graph", REPO / "scripts" / "build_interest_graph.py")
big = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(big)


def valid_payload() -> dict:
    return {
        "inferred_interests": [
            {
                "name": "Distributed Databases",
                "kind": "domain",
                "parent": None,
                "temporal_state": "durable",
                "stance": "learning",
                "confidence": 0.9,
                "observed_vs_inferred": "observed",
                "goal": "Build a replicated multi-region datastore",
                "information_need": "Which consensus algorithm survives "
                                    "partitioned minorities",
                "cluster_ids": [1, 2],
                "evidence_summary": "Clusters 1-2: 30 docs across 8 channels",
                "counterevidence": None,
                "related_to": ["Compiler Optimization"],
            },
            {
                "name": "Raft Consensus",
                "kind": "subtopic",
                "parent": "Distributed Databases",
                "temporal_state": "active",
                "stance": "project",
                "confidence": 0.8,
                "observed_vs_inferred": "observed",
                "goal": None,
                "information_need": None,
                "cluster_ids": [2],
                "evidence_summary": "Cluster 2: leader-election deep dives",
                "counterevidence": "single recreational lecture",
                "related_to": [],
            },
            {
                "name": "Compiler Optimization",
                "kind": "topic",
                "parent": None,
                "temporal_state": "emerging",
                "stance": "curiosity",
                "confidence": 0.6,
                "observed_vs_inferred": "inferred_adjacent",
                "goal": None,
                "information_need": "Cost model of profile-guided optimization",
                "cluster_ids": [3],
                "evidence_summary": "Cluster 3: PGO discussions",
                "counterevidence": None,
                "related_to": ["Distributed Databases"],
            },
        ],
        "questions": [
            {
                "text": "Does Raft make progress during a minority partition?",
                "interest": "Raft Consensus",
                "status": "open",
            },
        ],
        "regret_candidates": [
            {
                "topic": "Astronomy",
                "why": "Adjacent to observed physics consumption",
                "label": "inferred_adjacent",
                "confidence": 0.5,
                "cluster_ids": [4],
                "related_interests": ["Distributed Databases"],
            },
        ],
    }


SUPPLIED = {1, 2, 3, 4}


def _container(payload, dotted):
    parts = dotted.split(".")
    obj = payload
    for part in parts[:-1]:
        obj = obj[int(part)] if part.isdigit() else obj[part]
    last = parts[-1]
    return obj, (int(last) if last.isdigit() else last)


def mut(dotted, value):
    """Deep-copied valid payload with the dotted path set to value."""
    p = copy.deepcopy(valid_payload())
    obj, key = _container(p, dotted)
    obj[key] = value
    return p


def drop(dotted):
    """Deep-copied valid payload with the dotted path removed."""
    p = copy.deepcopy(valid_payload())
    obj, key = _container(p, dotted)
    del obj[key]
    return p


def rejects(payload, supplied=SUPPLIED):
    with pytest.raises(big.InferenceContractError):
        big.validate_inference(payload, supplied)


# ---------------------------------------------------------------------------
# Validation acceptance / rejection
# ---------------------------------------------------------------------------

def test_valid_payload_passes():
    big.validate_inference(valid_payload(), SUPPLIED)


def test_reject_wrong_top_level_type():
    rejects([valid_payload()])                        # array, not object
    rejects("interests")                              # string, not object
    rejects({k: v for k, v in valid_payload().items()
             if k != "questions"})                    # missing top-level array
    rejects(mut("inferred_interests", "nope"))
    rejects(mut("inferred_interests", {}))
    rejects(mut("inferred_interests", []))            # silently drops state


def test_reject_interest_field_violations():
    rejects(drop("inferred_interests.0.name"))
    rejects(mut("inferred_interests.0.name", "   "))
    rejects(mut("inferred_interests.0.kind", "hobby"))
    rejects(mut("inferred_interests.0.temporal_state", "fast"))
    rejects(mut("inferred_interests.0.stance", "obsessive"))
    rejects(mut("inferred_interests.0.observed_vs_inferred", "guessed"))
    rejects(drop("inferred_interests.0.evidence_summary"))
    rejects(mut("inferred_interests.0.evidence_summary", "  "))
    rejects(mut("inferred_interests.0.goal", "   "))     # not a real goal
    rejects(mut("inferred_interests.0.information_need", ""))
    rejects(mut("inferred_interests.0.counterevidence", 7))
    rejects(mut("inferred_interests.0.related_to", "Compiler Optimization"))


@pytest.mark.parametrize("confidence", [-0.1, 1.5, True, False, "high",
                                        None])
def test_reject_bad_confidence(confidence):
    rejects(mut("inferred_interests.0.confidence", confidence))


def test_reject_duplicate_interest_names_case_insensitive():
    p = mut("inferred_interests.2.name", "RAFT CONSENSUS")  # collides with [1]
    rejects(p)


def test_reject_cluster_id_violations():
    rejects(mut("inferred_interests.0.cluster_ids", [999]))   # not supplied
    rejects(mut("inferred_interests.0.cluster_ids", [1, 1]))  # duplicate
    rejects(mut("inferred_interests.0.cluster_ids", []))      # empty
    rejects(mut("inferred_interests.0.cluster_ids", "1,2"))
    rejects(mut("inferred_interests.0.cluster_ids", [1.0, 2]))
    rejects(mut("inferred_interests.0.cluster_ids", [True]))
    rejects(drop("inferred_interests.0.cluster_ids"))


def test_reject_parent_violations():
    rejects(mut("inferred_interests.0.parent", "Gardening"))  # unknown
    rejects(mut("inferred_interests.0.parent",
                "Distributed Databases"))                     # self parent
    rejects(mut("inferred_interests.0.parent", "  "))         # empty str


def test_reject_parent_cycle():
    p = mut("inferred_interests.0.parent", "Raft Consensus")
    # Raft -> Distributed Databases -> Raft closes the cycle.
    rejects(p)


def test_reject_related_to_unknown_target():
    rejects(mut("inferred_interests.0.related_to", ["Gardening"]))


def test_reject_question_violations():
    rejects(drop("questions.0.text"))
    rejects(mut("questions.0.text", "   "))
    rejects(mut("questions.0.status", "answered"))
    rejects(mut("questions.0.interest", "Gardening"))
    rejects(drop("questions.0.interest"))


def test_reject_regret_violations():
    rejects(mut("regret_candidates.0.label", "observed"))
    rejects(mut("regret_candidates.0.why", ""))
    rejects(mut("regret_candidates.0.topic", " "))
    rejects(mut("regret_candidates.0.confidence", 2.0))
    rejects(mut("regret_candidates.0.confidence", True))
    rejects(mut("regret_candidates.0.cluster_ids", [999]))
    rejects(mut("regret_candidates.0.related_interests", ["Gardening"]))


def test_optional_sections_may_be_empty():
    p = valid_payload()
    p["questions"] = []
    p["regret_candidates"] = []
    big.validate_inference(p, SUPPLIED)


# ---------------------------------------------------------------------------
# Provider output parsing / execution failure semantics
# ---------------------------------------------------------------------------

def test_extract_agent_message():
    events = "\n".join([
        json.dumps({"type": "session.start"}),
        json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": '{"ok": 1}'}}),
    ])
    assert big.extract_agent_message(events) == '{"ok": 1}'
    assert big.extract_agent_message("not jsonl at all") is None


def test_extract_json_object_fenced_and_bare():
    assert big.extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert big.extract_json_object('prose {"a": 1} prose') == {"a": 1}
    with pytest.raises(big.ProviderExecutionError):
        big.extract_json_object("no json here")


def synthetic_clusters():
    return [
        {"cluster_id": i, "label": f"cluster-{i}",
         "terms": [f"term{i}a", f"term{i}b"],
         "entities": [{"entity": f"Entity{i}"}],
         "channels": 2 + i, "documents": 10 + i, "active_months": 3,
         "first_month": "2026-01", "last_month": "2026-08",
         "sources": {"youtube": 5, "discord": 2}, "phase": "steady",
         "representative": [{"title": f"Doc {i}", "month": "2026-05",
                             "source": "youtube"}]}
        for i in range(1, 5)
    ]


class FakeCompleted:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _jsonl_of(payload) -> str:
    return json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": json.dumps(payload)}})


@pytest.fixture
def no_path_lookup(monkeypatch):
    """Provider binaries do not need to exist for mocked runs."""
    monkeypatch.setattr(big.shutil, "which", lambda name: name)


def test_run_inference_validates_and_writes_artifact(
        monkeypatch, tmp_path, no_path_lookup):
    payload = valid_payload()
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["creationflags"] = kwargs.get("creationflags")
        return FakeCompleted(0, _jsonl_of(payload))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result, meta = big.run_inference(
        provider="codex", clusters=synthetic_clusters(),
        prompt_path=tmp_path / "prompt.txt",
        result_path=tmp_path / "result.json")
    assert result == payload
    assert meta["cluster_ids"] == [1, 2, 3, 4]
    assert meta["requested_model"] == "gpt-5.6-luna"
    assert meta["prompt_version"] == big.PROMPT_VERSION
    assert meta["result_hash"] == big.canonical_result_hash(payload)
    # result files carry the validation envelope: a raw provider payload
    # is never written without its run_id + validated status stamp
    blob = json.loads(
        (tmp_path / "result.json").read_text(encoding="utf-8"))
    assert blob["payload"] == payload
    assert blob["validation_status"] == "validated"
    assert blob["result_hash"] == meta["result_hash"]
    assert seen["cmd"][0] == "codex"
    assert seen["creationflags"], "no-window creationflags required"


def test_run_inference_contract_violation_writes_no_artifact(
        monkeypatch, tmp_path, no_path_lookup):
    payload = mut("inferred_interests.0.cluster_ids", [999])

    def fake_run(cmd, **kwargs):
        return FakeCompleted(0, _jsonl_of(payload))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(big.InferenceContractError):
        big.run_inference(provider="codex", clusters=synthetic_clusters(),
                          prompt_path=tmp_path / "prompt.txt",
                          result_path=tmp_path / "result.json")
    assert not (tmp_path / "result.json").exists()


def test_run_inference_nonzero_exit_is_provider_failure(
        monkeypatch, tmp_path, no_path_lookup):
    long_stderr = "boom " + "x" * 5000

    def fake_run(cmd, **kwargs):
        return FakeCompleted(1, "", long_stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(big.ProviderExecutionError, match="exited 1") as exc:
        big.run_inference(provider="codex", clusters=synthetic_clusters(),
                          prompt_path=tmp_path / "prompt.txt",
                          result_path=tmp_path / "result.json")
    assert len(str(exc.value)) < 100 + big.STDERR_DIAGNOSTIC_LIMIT


def test_run_inference_no_agent_message_no_json(
        monkeypatch, tmp_path, no_path_lookup):
    events = (json.dumps({"type": "session.start"}) + "\n" +
              json.dumps({"type": "item.completed",
                          "item": {"type": "turn.end"}}))

    def fake_run(cmd, **kwargs):
        return FakeCompleted(0, events)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(big.ProviderExecutionError):
        big.run_inference(provider="codex", clusters=synthetic_clusters(),
                          prompt_path=tmp_path / "prompt.txt",
                          result_path=tmp_path / "result.json")


def test_run_inference_plain_stdout_provider(monkeypatch, tmp_path,
                                              no_path_lookup):
    """Non-JSONL providers (agy path) pass plain JSON through."""
    def fake_run(cmd, **kwargs):
        return FakeCompleted(0, json.dumps(valid_payload()))

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload, meta = big.run_inference(
        provider="agy", clusters=synthetic_clusters(),
        prompt_path=tmp_path / "prompt.txt",
        result_path=tmp_path / "result.json")
    assert meta["requested_model"] == "gemini/gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Fail-closed boundary (validation happens before any DB mutation)
# ---------------------------------------------------------------------------

def test_invalid_output_never_reaches_storage(monkeypatch, tmp_path,
                                               no_path_lookup):
    from ef import personal_graph as pg
    conn = pg.connect(str(tmp_path / "pg.sqlite"))
    payload = mut("questions.0.interest", "Not An Interest")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: FakeCompleted(
                            0, _jsonl_of(payload)))
    with pytest.raises(big.InferenceContractError):
        big.run_inference(provider="codex", clusters=synthetic_clusters(),
                          prompt_path=tmp_path / "prompt.txt",
                          result_path=tmp_path / "result.json")
    for table in ("interests", "goals", "information_needs", "questions",
                  "regret_candidates", "inference_runs"):
        assert conn.execute(
            f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    conn.close()


def test_canonical_result_hash_is_stable_for_identical_output():
    assert big.canonical_result_hash(valid_payload()) == \
        big.canonical_result_hash(valid_payload())


# ===========================================================================
# Full-coverage bounded bootstrap
# ===========================================================================

def cheap_entry(cid, **overrides):
    entry = {
        "cluster_id": cid, "label": f"cluster-{cid}",
        "member_count": 40 + cid, "video_count": 10 + cid,
        "channels": 4 + (cid % 7), "documents": 50 + 10 * cid,
        "active_months": 5 + (cid % 4),
        "first_month": "2025-01", "last_month": "2026-08",
        "phase": "emerging" if cid % 17 == 0 else None,
        "sources": [["youtube", 30], ["discord", 10 + cid % 5], ["hn", 5]],
        "terms": [f"term{cid}a", f"term{cid}b"],
        "evidence_signature": f"sig{cid:04d}",
    }
    entry.update(overrides)
    return entry


NARROW_CID = 58          # the distinctive narrow-interest cluster
NARROW_NAME = "Gardening soil chemistry"


def inventory_of(n=60, narrow=True):
    entries = []
    for cid in range(1, n + 1):
        if narrow and cid == NARROW_CID:
            entries.append(cheap_entry(
                cid, label="gardening-soil-chemistry", channels=3,
                documents=45, active_months=3,
                sources=[["youtube", 45], ["reddit", 2]]))
        else:
            entries.append(cheap_entry(cid))
    return {"clusters": entries, "eligible_count": len(entries),
            "total_semantic_non_series": len(entries) + 3,
            "exclusions": {"series": 2, "member_count_below_floor": 1,
                           "channels_below_floor": 0}}


def synth_packet(cid, label=None):
    return {"cluster_id": cid, "label": label or f"cluster-{cid}",
            "terms": [f"t{cid}a", f"t{cid}b"],
            "entities": [{"entity": f"Entity{cid}", "videos": 5,
                          "specificity": 0.5}],
            "channels": 4, "documents": 10 + cid,
            "videos": 10 + cid, "active_months": 4,
            "first_month": "2026-01", "last_month": "2026-07",
            "sources": [["youtube", 5 + cid]],
            "phase": None,
            "representative": [{"title": f"Doc {cid}", "month": "2026-05",
                                "source": "youtube"}]}


def make_fake_invoke(special=None, merge_pairs=False):
    """Dispatcher faking both seams (batch inference + reconciliation).

    Batch prompts emit one interest per cluster (paired two-per-interest
    when merge_pairs=True so reconciliation stays single-stage for 60
    clusters); special names a cluster's unique interest. Reconciliation
    prompts are identity-merged (kept, one fragment per interest)."""
    import re

    def invoke(provider, prompt, prompt_file, timeout):
        if "Fragments (JSON):" in prompt:
            blob = prompt.split("Fragments (JSON):\n", 1)[1] \
                        .split("\n\nReconcile them", 1)[0]
            frags = json.loads(blob)
            final_interests, dispositions = [], []
            for f in frags:
                it = {k: v for k, v in f.items()
                      if k not in ("fragment_id", "batch_id")}
                final_interests.append(it)
                dispositions.append({
                    "fragment_id": f["fragment_id"], "decision": "kept",
                    "target_interest": it["name"],
                    "reason": "identity reconciliation for test"})
            return ({"final": {"inferred_interests": final_interests,
                               "questions": [], "regret_candidates": []},
                     "fragment_dispositions": dispositions}, "fake-model")

        cids = sorted(int(m) for m in
                      re.findall(r"^Cluster (\d+):", prompt, re.M))
        interests = []
        if merge_pairs:
            for i in range(0, len(cids), 2):
                pair = cids[i:i + 2]
                special_name = next(
                    (special[c] for c in pair if c in (special or {})), None)
                name = special_name or \
                    f"Interest of clusters {pair[0]}-{pair[-1]}"
                interests.append({
                    "name": name, "kind": "topic", "parent": None,
                    "temporal_state": "active", "stance": "learning",
                    "confidence": 0.7,
                    "observed_vs_inferred": "observed", "goal": None,
                    "information_need": None, "cluster_ids": pair,
                    "evidence_summary": f"clusters {pair}",
                    "counterevidence": None, "related_to": []})
        else:
            for cid in cids:
                name = (special or {}).get(cid, f"Interest of cluster {cid}")
                interests.append({
                    "name": name, "kind": "topic", "parent": None,
                    "temporal_state": "active", "stance": "learning",
                    "confidence": 0.7,
                    "observed_vs_inferred": "observed", "goal": None,
                    "information_need": None, "cluster_ids": [cid],
                    "evidence_summary": f"cluster {cid} evidence",
                    "counterevidence": None, "related_to": []})
        return ({"inferred_interests": interests, "questions": [],
                 "regret_candidates": []}, "fake-model")

    return invoke


# --- fragments -------------------------------------------------------------

def test_fragment_ids_deterministic():
    plan = "plan_abc123"
    f1 = big.build_fragments(plan, "b001", valid_payload())
    f2 = big.build_fragments(plan, "b001", valid_payload())
    assert [i["fragment_id"] for i in f1["interests"]] == \
        [i["fragment_id"] for i in f2["interests"]]
    other = big.build_fragments(plan, "b002", valid_payload())
    assert f1["interests"][0]["fragment_id"] != \
        other["interests"][0]["fragment_id"]
    assert all(i["fragment_id"].startswith("frag_")
               for i in f1["interests"])


# --- batch inference --------------------------------------------------------

def test_run_batch_inference_uses_exact_batch_ids(monkeypatch, tmp_path):
    from ef.interest_candidates import build_bootstrap_plan
    plan = build_bootstrap_plan(inventory_of(n=30)["clusters"])
    batch = plan.batches[0]
    captured = {}

    def invoke(provider, prompt, prompt_file, timeout):
        captured["prompt"] = prompt
        import re
        cids = sorted(int(m) for m in
                      re.findall(r"^Cluster (\d+):", prompt, re.M))
        interest = {"name": f"Interest of cluster {cids[0]}",
                    "kind": "topic", "parent": None,
                    "temporal_state": "active", "stance": "learning",
                    "confidence": 0.7, "observed_vs_inferred": "observed",
                    "goal": None, "information_need": None,
                    "cluster_ids": [cids[0]],
                    "evidence_summary": "evidence", "counterevidence": None,
                    "related_to": []}
        return ({"inferred_interests": [interest], "questions": [],
                 "regret_candidates": []}, "fake-model")

    monkeypatch.setattr(big, "_invoke_and_extract", invoke)
    fragments, meta = big.run_batch_inference(
        plan.plan_id, batch, [synth_packet(c) for c in batch.cluster_ids],
        prompt_path=tmp_path / "p.txt")
    import re
    in_prompt = {int(m) for m in
                 re.findall(r"^Cluster (\d+):", captured["prompt"], re.M)}
    assert in_prompt == set(batch.cluster_ids)
    assert meta["cluster_ids"] == list(batch.cluster_ids)
    assert meta["requested_model"] == "fake-model"


def test_run_batch_inference_rejects_foreign_cluster(monkeypatch, tmp_path):
    from ef.interest_candidates import build_bootstrap_plan
    plan = build_bootstrap_plan(inventory_of(n=26)["clusters"])
    batch = plan.batches[0]

    def invoke(provider, prompt, prompt_file, timeout):
        payload = valid_payload()   # cites clusters {1,2,3,4}-shaped refs
        return payload, "fake-model"

    monkeypatch.setattr(big, "_invoke_and_extract", invoke)
    with pytest.raises(big.InferenceContractError):
        big.run_batch_inference(
            plan.plan_id, batch,
            [synth_packet(c) for c in batch.cluster_ids],
            prompt_path=tmp_path / "p.txt")


# --- reconciliation contract -------------------------------------------------

def leaf_fragment(name, cids, fid):
    return {"fragment_id": fid, "batch_id": "b001",
            "interest": {"name": name, "kind": "topic", "parent": None,
                         "temporal_state": "active", "stance": "learning",
                         "confidence": 0.7,
                         "observed_vs_inferred": "observed", "goal": None,
                         "information_need": None,
                         "cluster_ids": list(cids),
                         "evidence_summary": f"evidence {name}",
                         "counterevidence": None, "related_to": []},
            "cluster_ids": list(cids)}


def recon_wrapper(interests, dispositions):
    return {"final": {"inferred_interests": interests, "questions": [],
                      "regret_candidates": []},
            "fragment_dispositions": dispositions}


def kept(fid, name):
    return {"fragment_id": fid, "decision": "kept",
            "target_interest": name, "reason": "well supported"}


def test_validate_reconciliation_accepts_valid():
    frags = [leaf_fragment("Distributed Databases", [1], "f1"),
             leaf_fragment("Compiler Optimization", [2], "f2")]
    interests = [dict(frags[0]["interest"], cluster_ids=[1]),
                 dict(frags[1]["interest"], cluster_ids=[2])]
    big.validate_reconciliation(
        recon_wrapper(interests, [kept("f1", "Distributed Databases"),
                                  kept("f2", "Compiler Optimization")]),
        frags, {1, 2, 3, 4})


def _expect_recon_error(wrapper, frags, allowed={1, 2, 3, 4}):
    with pytest.raises(big.ReconciliationContractError):
        big.validate_reconciliation(wrapper, frags, allowed)


def test_validate_reconciliation_rejections():
    frags = [leaf_fragment("A", [1], "f1"), leaf_fragment("B", [2], "f2")]
    ia = dict(frags[0]["interest"], cluster_ids=[1])
    ib = dict(frags[1]["interest"], cluster_ids=[2])
    ok = [kept("f1", "A"), kept("f2", "B")]

    _expect_recon_error(recon_wrapper([ia, ib], [kept("f9", "A")] + ok[1:]),
                        frags)                                   # unknown id
    _expect_recon_error(recon_wrapper([ia, ib], [ok[0]]), frags)  # missing
    _expect_recon_error(recon_wrapper([ia, ib], ok + [ok[0]]), frags)  # dup
    bad_decision = [dict(kept("f1", "A"), decision="lost"), ok[1]]
    _expect_recon_error(recon_wrapper([ia, ib], bad_decision), frags)
    unknown_target = [dict(kept("f1", "Gardening")), ok[1]]
    _expect_recon_error(recon_wrapper([ia, ib], unknown_target), frags)
    no_reason = [dict(kept("f1", "A"), decision="discarded",
                      target_interest=None, reason="  "), ok[1]]
    _expect_recon_error(recon_wrapper([ia, ib], no_reason), frags)
    invented = [dict(ia, cluster_ids=[1, 3]), ib]
    _expect_recon_error(recon_wrapper(invented, ok), frags)  # cid 3 unassigned
    _expect_recon_error({"no": "wrapper"}, frags)             # wrong shape
    _expect_recon_error(recon_wrapper([ia, ib], "nope"), frags)  # bad list


# --- bounded tree ------------------------------------------------------------

def test_reconciliation_stage_structure_bounds():
    assert big.reconciliation_stage_structure(40) == [[40]]
    assert big.reconciliation_stage_structure(41) == [[40, 1], [2]]
    assert big.reconciliation_stage_structure(100) == [[40, 40, 20], [3]]
    assert big.reconciliation_stage_structure(0) == []


def merging_invoke(provider, prompt, prompt_file, timeout):
    """Reconciler that merges fragments sharing a normalized name."""
    blob = prompt.split("Fragments (JSON):\n", 1)[1] \
                .split("\n\nReconcile them", 1)[0]
    frags = json.loads(blob)
    by_name = {}
    order = []
    for f in frags:
        key = " ".join(f["name"].strip().casefold().split())
        if key not in by_name:
            by_name[key] = {"name": f["name"], "cluster_ids": set()}
            order.append(key)
        by_name[key]["cluster_ids"].update(f["cluster_ids"])
    interests = []
    for key in order:
        merged = by_name[key]
        interests.append({"name": merged["name"], "kind": "topic",
                          "parent": None, "temporal_state": "active",
                          "stance": "learning", "confidence": 0.7,
                          "observed_vs_inferred": "observed", "goal": None,
                          "information_need": None,
                          "cluster_ids": sorted(merged["cluster_ids"]),
                          "evidence_summary": "merged",
                          "counterevidence": None, "related_to": []})
    name_by_key = {i["name"]:
                   " ".join(i["name"].strip().casefold().split())
                   for i in interests}
    dispositions = []
    for f in frags:
        key = " ".join(f["name"].strip().casefold().split())
        target = next(n for n, k in name_by_key.items() if k == key)
        dispositions.append({"fragment_id": f["fragment_id"],
                             "decision": "merged",
                             "target_interest": target,
                             "reason": "same interest across batches"})
    return ({"final": {"inferred_interests": interests, "questions": [],
                       "regret_candidates": []},
             "fragment_dispositions": dispositions}, "fake-model")


def make_named_fragments(n_names, copies=2):
    """n_names*copies leaf fragments where every `copies` share a name."""
    frags = []
    for i in range(n_names):
        for c in range(copies):
            frags.append(leaf_fragment(
                f"Interest {i}", [i * copies + c + 1],
                f"frag_{i:03d}{c}"))
    return sorted(frags, key=lambda f: f["fragment_id"])


def test_bounded_tree_recursive_and_audited():
    frags = make_named_fragments(25, copies=2)      # 50 fragments, 25 names
    result = big.run_reconciliation_tree(
        {"interests": frags, "questions": [], "regret_candidates": []},
        list(range(1, 51)), invoke=merging_invoke)
    assert len(result["stages"]) == 2               # 50 -> 25 -> 1 call
    assert result["provider_calls"] == 3            # 2 groups + final
    assert len(result["fragment_dispositions"]) == 50
    assert all(d["decision"] == "merged" for d in result["fragment_dispositions"])
    assert len(result["final"]["inferred_interests"]) == 25


def test_bounded_tree_fails_closed_when_no_reduction():
    frags = [leaf_fragment(f"Unique {i}", [i + 1], f"frag_u{i}")
             for i in range(45)]                     # 45 unique names
    with pytest.raises(big.ReconciliationContractError, match="reduce"):
        big.run_reconciliation_tree(
            {"interests": frags, "questions": [], "regret_candidates": []},
            list(range(1, 46)), invoke=merging_invoke)


def test_flatten_leaf_dispositions_walks_stages():
    # unit-level: leaf -> intermediate -> final chain
    stage_records = [
        {"stage": 1, "group_sizes": [40, 1], "dispositions": [
            {"fragment_id": "leaf_a", "decision": "merged",
             "target_interest": "Deep Topic", "reason": "same topic"},
            {"fragment_id": "leaf_b", "decision": "discarded",
             "target_interest": None, "reason": "noise"},
        ], "outputs": {"deep topic": "inter_1"}},
        {"stage": 2, "group_sizes": [2], "dispositions": [
            {"fragment_id": "inter_1", "decision": "kept",
             "target_interest": "Deep Topic Final", "reason": "supported"},
        ], "outputs": {}},
    ]
    leaves = [leaf_fragment("Deep Topic", [1], "leaf_a"),
              leaf_fragment("Noise", [2], "leaf_b")]
    out = {d["fragment_id"]: d for d in
           big._flatten_leaf_dispositions(stage_records, leaves)}
    assert out["leaf_a"]["decision"] == "kept"
    assert out["leaf_a"]["target_interest"] == "Deep Topic Final"
    assert out["leaf_b"]["decision"] == "discarded"


# --- bootstrap runner ---------------------------------------------------------

def run_fake_bootstrap(tmp_path, monkeypatch, inventory, invoke, **kw):
    monkeypatch.setattr(big, "_invoke_and_extract", invoke)
    return big.run_bootstrap(
        allow_spend=True, artifact_root=tmp_path, inventory=inventory,
        hydrate=lambda ids: [synth_packet(c) for c in ids],
        invoke=invoke, **kw)


def test_run_bootstrap_requires_allow_spend(tmp_path):
    with pytest.raises(PermissionError):
        big.run_bootstrap(allow_spend=False, artifact_root=tmp_path,
                          inventory=inventory_of(n=30),
                          hydrate=lambda ids: [synth_packet(c) for c in ids])


def test_run_bootstrap_happy_path_artifacts(tmp_path, monkeypatch):
    invoke = make_fake_invoke(merge_pairs=True)
    result = run_fake_bootstrap(tmp_path, monkeypatch, inventory_of(n=30),
                                invoke)
    run_dir = Path(result["run_dir"])
    assert result["summary"]["status"] == "success"
    assert result["summary"]["batches"] == 2
    assert result["summary"]["provider_calls"] == 3   # 2 batches + 1 recon
    for name in ("plan.json", "inventory-summary.json",
                 "batch-01-input-metadata.json",
                 "batch-01-validated-result.json",
                 "batch-02-input-metadata.json",
                 "batch-02-validated-result.json",
                 "reconciliation-stage-01.json",
                 "final-validated-result.json", "run-summary.json"):
        assert (run_dir / name).exists(), f"missing artifact {name}"
    assert str(run_dir).startswith(str(tmp_path))     # outside source tree
    covered = set()
    for it in result["final"]["inferred_interests"]:
        covered.update(it["cluster_ids"])
    assert covered == set(range(1, 31))


def test_run_bootstrap_fail_closed_on_bad_batch(tmp_path, monkeypatch):
    good = make_fake_invoke(merge_pairs=True)

    def invoke(provider, prompt, prompt_file, timeout):
        if "Fragments (JSON):" in prompt:
            return good(provider, prompt, prompt_file, timeout)
        payload, model = good(provider, prompt, prompt_file, timeout)
        if "Cluster 26:" in prompt:          # batch 2 covers clusters 26-30
            payload = valid_payload()        # wrong cluster refs
        return payload, model

    with pytest.raises(big.InferenceContractError):
        run_fake_bootstrap(tmp_path, monkeypatch, inventory_of(n=30), invoke)
    summaries = list(Path(tmp_path).rglob("run-summary.json"))
    assert summaries and json.loads(
        summaries[0].read_text(encoding="utf-8"))["status"] == "failed"
    assert not list(Path(tmp_path).rglob("final-validated-result.json"))


def test_run_bootstrap_fail_closed_on_reconciliation(tmp_path, monkeypatch):
    base = make_fake_invoke(merge_pairs=True)

    def invoke(provider, prompt, prompt_file, timeout):
        if "Fragments (JSON):" not in prompt:
            return base(provider, prompt, prompt_file, timeout)
        wrapper, model = base(provider, prompt, prompt_file, timeout)
        wrapper["fragment_dispositions"] = wrapper["fragment_dispositions"][:-1]
        return wrapper, model                    # silently drops a fragment

    with pytest.raises(big.ReconciliationContractError):
        run_fake_bootstrap(tmp_path, monkeypatch, inventory_of(n=30), invoke)
    assert not list(Path(tmp_path).rglob("final-validated-result.json"))


# --- discriminating root-cause test (§18) ------------------------------------

def test_baseline_misses_narrow_cluster_bootstrap_recovers_it(
        tmp_path, monkeypatch):
    from ef.interest_candidates import (build_baseline_plan,
                                        build_bootstrap_plan, plan_coverage)
    inventory = inventory_of(n=60)

    # A: baseline top-25 structurally excludes the narrow cluster.
    baseline = build_baseline_plan(inventory["clusters"])
    assert NARROW_CID not in {c for b in baseline.batches
                              for c in b.cluster_ids}
    assert baseline.metrics.planned_count == 25
    assert baseline.metrics.dropped_count == 35

    # B: bootstrap covers every eligible cluster exactly once.
    bootstrap = build_bootstrap_plan(inventory["clusters"])
    cov = plan_coverage(bootstrap)
    assert cov["covered"] == 60 and cov["eligible"] == 60
    assert not cov["missing_cluster_ids"] and not cov["duplicate_cluster_ids"]
    assert NARROW_CID in {c for b in bootstrap.batches
                          for c in b.cluster_ids}

    # C: mocked end-to-end bootstrap retains the unique narrow interest.
    invoke = make_fake_invoke(special={NARROW_CID: NARROW_NAME},
                              merge_pairs=True)
    result = run_fake_bootstrap(tmp_path, monkeypatch, inventory, invoke)
    assert result["summary"]["status"] == "success"
    names = {it["name"] for it in result["final"]["inferred_interests"]}
    assert NARROW_NAME in names
    narrow = next(it for it in result["final"]["inferred_interests"]
                  if it["name"] == NARROW_NAME)
    assert NARROW_CID in narrow["cluster_ids"]
    # no top-N truncation: every eligible cluster appears in the final graph
    covered = set()
    for it in result["final"]["inferred_interests"]:
        covered.update(it["cluster_ids"])
    assert covered == set(range(1, 61))
