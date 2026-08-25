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
    assert (tmp_path / "result.json").read_text(encoding="utf-8") == \
        json.dumps(payload, indent=2, ensure_ascii=False)
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
