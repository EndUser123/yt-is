"""Run-scoped artifact isolation tests (additive packet 2026-08-26).

Proves the enumerated concurrency/hygiene requirements offline:
unique per-run artifact homes, no cross-run overwrite, no canonical
pointer on failed validation/reconciliation, no implicit selection of
stale artifacts, explicit provenance for persistence, retry freshness,
and zero residual dependency on the retired fixed global path.
"""

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

from ef.inference_contract import inference_output_schema  # noqa: F401

_hspec = importlib.util.spec_from_file_location(
    "tbig_helpers", REPO / "tests" / "test_build_interest_graph.py")
tbig = importlib.util.module_from_spec(_hspec)
_hspec.loader.exec_module(tbig)

FakeCompleted = tbig.FakeCompleted
_jsonl_of = tbig._jsonl_of
inventory_of = tbig.inventory_of
synth_packet = tbig.synth_packet
synthetic_clusters = tbig.synthetic_clusters


@pytest.fixture
def no_path_lookup(monkeypatch):
    monkeypatch.setattr(big.shutil, "which", lambda name: name)


BANNED_RESULT_PATH = "P:/tmp/interest-inference-result.json"


def valid_payload():
    return {
        "inferred_interests": [
            {"name": "Distributed Databases", "kind": "domain",
             "parent": None, "temporal_state": "durable",
             "stance": "learning", "confidence": 0.9,
             "observed_vs_inferred": "observed", "goal": None,
             "information_need": None, "cluster_ids": [1],
             "evidence_summary": "s", "counterevidence": None,
             "related_to": []}],
        "questions": [],
        "regret_candidates": [],
    }


def test_new_run_dir_yields_distinct_paths_every_call():
    # requirements 1, 2 and 7: identity unique per execution even within
    # one wall-clock second, so retries never reuse another run's home
    roots = {str(big._new_run_dir("single-shot")) for _ in range(5)}
    assert len(roots) == 5
    for r in roots:
        assert str(big.ARTIFACT_ROOT) in r
        assert "/runs/" in r.replace("\\", "/")


def test_bootstrap_reruns_get_fresh_run_dirs(tmp_path, monkeypatch):
    """Requirement 7: retrying a run gets a brand-new automatically-scoped
    run directory, so a retry can never consume the previous attempt's
    artifacts."""
    inv = inventory_of(60)
    monkeypatch.setattr(big, "ARTIFACT_ROOT", tmp_path)

    def batch_invoke(provider, prompt, prompt_file, timeout):
        # fabricate ONE validator-clean interest citing this batch's own
        # first cluster id, whatever the planner ordered
        import re
        ids = [int(m) for m in
               re.findall(r"^Cluster (\d+):", prompt, re.M)]
        cid = ids[0]
        payload = valid_payload()
        it = payload["inferred_interests"][0]
        it["name"] = f"Batch Interest {cid}"
        it["cluster_ids"] = [cid]
        it["evidence_summary"] = f"cluster {cid}"
        return payload, "m"

    monkeypatch.setattr(big, "_invoke_and_extract", batch_invoke)
    monkeypatch.setattr(big, "run_reconciliation_tree",
                        lambda *a, **k: {
                            "final": valid_payload(),
                            "fragment_dispositions": [],
                            "stages": [{"stage": 1, "group_sizes": [1],
                                        "dispositions": [], "outputs": {}}],
                            "provider_calls": 0})
    runs = []
    for _ in range(2):
        result = big.run_bootstrap(
            allow_spend=True, inventory=inv,
            hydrate=lambda ids: [synth_packet(c) for c in ids])
        runs.append(result)

    assert Path(runs[0]["run_dir"]) != Path(runs[1]["run_dir"])
    for r in runs:
        d = Path(r["run_dir"])
        assert (d / "run-summary.json").exists()
        # each run owns its batch prompts inside its own directory
        assert list((d / "prompts").glob("*.txt")), d


def test_recon_tree_default_prompts_are_run_scoped(tmp_path):
    """No-path reconciliation calls get per-tree unique prompt roots."""
    captured = []

    def fake_invoke(provider, prompt, prompt_file, timeout):
        captured.append(Path(prompt_file))
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        return {"final": {"inferred_interests":
                              [frag_for("A", 1)["interest"],
                               frag_for("B", 2)["interest"]],
                          "questions": [], "regret_candidates": []},
                "fragment_dispositions": [
                    {"fragment_id": "f1", "decision": "kept",
                     "target_interest": "A", "reason": "ok"},
                    {"fragment_id": "f2", "decision": "kept",
                     "target_interest": "B", "reason": "ok"}]}, "m"

    def frag_for(name, cid):
        return {"fragment_id": f"f{cid}", "batch_id": "b001",
                "interest": {"name": name, "kind": "topic", "parent": None,
                             "temporal_state": "active",
                             "stance": "learning", "confidence": 0.7,
                             "observed_vs_inferred": "observed",
                             "goal": None, "information_need": None,
                             "cluster_ids": [cid],
                             "evidence_summary": f"e {name}",
                             "counterevidence": None, "related_to": []},
                "cluster_ids": [cid]}

    frags = {"interests": [frag_for("A", 1), frag_for("B", 2)],
             "questions": [], "regret_candidates": []}
    big.run_reconciliation_tree(frags, [1, 2], invoke=fake_invoke)
    assert captured
    for p in captured:
        assert "P:/tmp" not in str(p).replace("\\", "/"), p
        assert (big.ARTIFACT_ROOT / "runs") in p.parents


def test_failed_validation_leaves_no_result_pointer(monkeypatch, tmp_path,
                                                    no_path_lookup):

    def fake_run(cmd, **kwargs):
        return FakeCompleted(0, _jsonl_of(valid_payload()))

    monkeypatch.setattr(subprocess, "run", fake_run)

    def boom(payload, supplied):
        raise big.InferenceContractError("forced validation failure")

    monkeypatch.setattr(big, "validate_inference", boom)
    run_root = tmp_path / "run"
    with pytest.raises(big.InferenceContractError):
        big.run_inference(provider="codex",
                          clusters=synthetic_clusters(),
                          run_root=run_root)
    files = sorted(p.name for p in run_root.rglob("*") if p.is_file())
    assert files == ["prompt.txt"], \
        f"a failed run must not leave a result pointer, found {files}"


def test_failed_bootstrap_leaves_no_validated_marker(tmp_path, monkeypatch):
    inv = inventory_of(60)

    def bad_invoke(provider, prompt, prompt_file, timeout):
        bad = valid_payload()
        bad["inferred_interests"][0]["cluster_ids"] = [999]
        return bad, "m"

    # batches execute through the module-level provider seam
    monkeypatch.setattr(big, "_invoke_and_extract", bad_invoke)
    with pytest.raises(big.InferenceContractError):
        big.run_bootstrap(allow_spend=True, artifact_root=tmp_path,
                          inventory=inv,
                          hydrate=lambda ids:
                              [synth_packet(c) for c in ids])
    summaries = [json.loads(p.read_text(encoding="utf-8"))
                 for p in list(tmp_path.glob("*/run-summary.json")) +
                 [tmp_path / "run-summary.json"]
                 if p.exists()]
    assert any(s.get("status") == "failed" for s in summaries)
    assert not (list(tmp_path.glob("**/final-validated-result.json")))


def test_no_implicit_selection_of_stale_artifacts(monkeypatch, tmp_path,
                                                  no_path_lookup):

    reads = []
    real_read_text = Path.read_text

    def spy_read_text(self, *a, **k):
        reads.append(str(self))
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    def fake_run(cmd, **kwargs):
        return FakeCompleted(0, _jsonl_of(valid_payload()))

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload, meta = big.run_inference(provider="codex",
                                      clusters=synthetic_clusters(),
                                      run_root=tmp_path / "fresh")
    assert payload == valid_payload()
    assert [r for r in reads if BANNED_RESULT_PATH in r] == []
    assert meta["result_hash"] == big.canonical_result_hash(payload)


def test_result_files_carry_explicit_provenance(monkeypatch, tmp_path,
                                                no_path_lookup):

    def fake_run(cmd, **kwargs):
        return FakeCompleted(0, _jsonl_of(valid_payload()))

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload, meta = big.run_inference(provider="codex",
                                      clusters=synthetic_clusters(),
                                      run_root=tmp_path / "runX")
    blob = json.loads((tmp_path / "runX" / "result.validated.json")
                      .read_text(encoding="utf-8"))
    assert meta["run_id"] == "runX"
    assert blob["validation_status"] == "validated"
    assert blob["payload"] == payload
    assert blob["result_hash"] == meta["result_hash"]


def test_no_fixed_global_path_dependency_remains():
    sources = [(REPO / "scripts" / "build_interest_graph.py"),
               (REPO / "scripts" / "interest_contract_bakeoff.py")]
    for src in sources:
        text = src.read_text(encoding="utf-8")
        assert BANNED_RESULT_PATH not in text, src
    assert not Path(BANNED_RESULT_PATH).exists()


def test_legacy_defaults_resolve_under_runs_root():
    src = (REPO / "scripts" /
           "build_interest_graph.py").read_text(encoding="utf-8")
    assert 'run_root / "prompt.txt"' in src
    assert 'run_root / "result.validated.json"' in src
    assert '_new_run_dir("single-shot")' in src
    assert '_new_run_dir("recon")' in src
