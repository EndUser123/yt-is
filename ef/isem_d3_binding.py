"""ISEM <-> D3 pre-unseal binding library (BINDING_AMENDMENT_2).

Reconstructs the three frozen D3 contestant payloads from the persisted
provider artifacts of their shadow runs and verifies the pre-unseal
binding manifest. This module is deliberately PURE: no subprocess, no
network, no store reads, no provider transport of any kind. The frozen
plan comes from each run root's plan.json (never from the live evidence
store), so reconstruction cannot touch the corpus, the holdout, or a
provider. tests/test_isem_d3_binding.py scans this file's source to pin
that property.

Reconstruction procedure DETERMINISTIC_ASSEMBLY_REPLAY_V1 (per the
freeze JSON's final_payload_reconstruction_note, independently
reproduced byte-identically by the Contract Reliability reviewer):

  1. load plan.json            -> eligible cluster ids + batch order
  2. per batch, in plan order: phase1/<bid>-phase1.validated.json
     -> re-run ef.contract_v2.validate_phase1_payload + build_inventory
  3. grouping[-completeness].raw.jsonl agent_message
     -> verify_group_coverage -> assemble_canonical
  4. relations/prompts/relations.raw.jsonl agent_message
     -> verify_relations -> apply_relations_to_assembly
  5. strict frozen validate_inference (build_interest_graph, manifest
     blob version) over the assembled payload
  6. canonical payload sha256 over the recipe-pinned serialization;
     exact equality with the bound contestant hash is REQUIRED

A hash mismatch is fatal (CONTESTANT_RECONSTRUCTION_MISMATCH): no
replacement runs, no regeneration — the frozen contestants are
immutable (inference-candidate-d3-freeze.json, commit f7bd24fd).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

RECONSTRUCTION_PROCEDURE = "DETERMINISTIC_ASSEMBLY_REPLAY_V1"

BOUND_CONTESTANT_IDS = ("shadow_1", "shadow_2", "shadow_3")

BINDING_STATUS = "AMENDMENT_4_READY_FOR_FRESH_PRE_UNSEAL_REVIEW"

# The sealed holdout is never an input of this module: the expected
# public hash is echoed from the freeze documents only.
EXPECTED_SEALED_GT_SHA256 = (
    "1c7885081dcb6a61e419273c42b3326428727f6718f47736582717b8535aa48f")


class BindingRefusal(Exception):
    """Fail-closed binding verification refusal."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _load_big():
    spec = importlib.util.spec_from_file_location(
        "isem_binding_build_interest_graph",
        REPO / "scripts" / "build_interest_graph.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Canonical serializations
# ---------------------------------------------------------------------------

def canonical_serialization_variants(assembled: dict) -> dict[str, bytes]:
    """Candidate canonical encodings of an assembled payload.

    The freeze computed payload_canonical_sha256 over one of these
    encodings; verification requires ONE recipe to reproduce all three
    bound hashes (three independent payloads cannot collide by accident).
    """
    out = {}
    for name, (kwargs, newline) in {
        "json.dumps(sort_keys)": (dict(sort_keys=True), False),
        "json.dumps(sort_keys)+nl": (dict(sort_keys=True), True),
        "json.dumps(sort_keys,compact)": (
            dict(sort_keys=True, separators=(",", ":")), False),
        "json.dumps(sort_keys,ensure_ascii=False)": (
            dict(sort_keys=True, ensure_ascii=False), False),
        "json.dumps(sort_keys,ensure_ascii=False)+nl": (
            dict(sort_keys=True, ensure_ascii=False), True),
        "json.dumps(sort_keys,compact,ensure_ascii=False)": (
            dict(sort_keys=True, separators=(",", ":"),
                 ensure_ascii=False), False),
        "json.dumps(indent=2,sort_keys)": (
            dict(indent=2, sort_keys=True), False),
        "json.dumps(indent=2,sort_keys)+nl": (
            dict(indent=2, sort_keys=True), True),
        "json.dumps(indent=2)": (dict(indent=2), False),
        "json.dumps(indent=2)+nl": (dict(indent=2), True),
        "json.dumps(indent=2,ensure_ascii=False)": (
            dict(indent=2, ensure_ascii=False), False),
        "json.dumps(indent=2,ensure_ascii=False)+nl": (
            dict(indent=2, ensure_ascii=False), True),
    }.items():
        text = json.dumps(assembled, **kwargs)
        out[name] = (text + "\n").encode("utf-8") if newline \
            else text.encode("utf-8")
    return out


# ---------------------------------------------------------------------------
# Frozen freeze-document loading
# ---------------------------------------------------------------------------

def load_freeze(freeze_path) -> dict:
    """Load and shape-check the D3 freeze JSON."""
    freeze = json.loads(Path(freeze_path).read_text(encoding="utf-8"))
    if freeze.get("document_kind") != "INFERENCE_CANDIDATE_D3_FREEZE":
        raise BindingRefusal(
            "FREEZE_DOCUMENT_UNRECOGNIZED",
            f"document_kind={freeze.get('document_kind')!r}")
    outputs = freeze.get("contestant_outputs") or {}
    ids = tuple(sorted(outputs))
    if ids != tuple(sorted(BOUND_CONTESTANT_IDS)):
        raise BindingRefusal(
            "CONTESTANT_SET_MISMATCH",
            f"freeze binds {ids}, expected exactly "
            f"{tuple(sorted(BOUND_CONTESTANT_IDS))}")
    for cid in BOUND_CONTESTANT_IDS:
        entry = outputs[cid]
        for key in ("run_root", "payload_canonical_sha256",
                    "code_manifest_sha256"):
            if not entry.get(key):
                raise BindingRefusal(
                    "FREEZE_DOCUMENT_INCOMPLETE",
                    f"{cid} missing {key}")
        if entry["code_manifest_sha256"] != \
                freeze.get("implementation_manifest_sha256"):
            raise BindingRefusal(
                "FREEZE_DOCUMENT_INCONSISTENT",
                f"{cid} code_manifest_sha256 != implementation manifest")
    if not freeze.get("runtime_configuration", {}).get(
            "config_identity_complete"):
        raise BindingRefusal(
            "FREEZE_DOCUMENT_INCOMPLETE",
            "runtime_configuration.config_identity_complete is not true")
    return freeze


# ---------------------------------------------------------------------------
# Implementation-manifest re-verification (drift guard)
# ---------------------------------------------------------------------------

def implementation_manifest_digest(manifest_list: list) -> str:
    """Canonical digest of the six-file manifest list."""
    return sha256_bytes(json.dumps(
        manifest_list, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))


def verify_implementation_manifest(repo_root, freeze: dict) -> dict:
    """Recompute every manifest file's content sha256 from the tree.

    BINDING_AMENDMENT_2 / review finding F2: the frozen manifest's
    content_sha256 values were taken over the lane's CRLF working-tree
    files, while the landed repository blobs are LF-normalized
    (blob sha1 identity is exact). Both forms are verified here and the
    matched EOL form is recorded per file — neither is silently
    rewritten. A file matching NEITHER form refuses.

    The tree must be byte-identical to the frozen implementation commit
    for all six load-bearing files; any content drift refuses (the
    evaluator may only bind an unchanged inference implementation).
    """
    problems = []
    checked = []
    for entry in freeze["implementation_manifest"]:
        p = Path(repo_root) / entry["path"]
        if not p.exists():
            problems.append(f"missing manifest file {entry['path']}")
            continue
        raw = p.read_bytes()
        canonical = raw.replace(b"\r\n", b"\n")
        forms = {
            "landed-lf": hashlib.sha256(canonical).hexdigest(),
            "working-crlf-variant":
                hashlib.sha256(raw.replace(b"\n", b"\r\n")).hexdigest(),
            "exact-bytes": hashlib.sha256(raw).hexdigest(),
        }
        matched_form = next((form for form, digest in forms.items()
                             if digest == entry["content_sha256"]), None)
        checked.append({"path": entry["path"],
                        "sha256": forms["exact-bytes"],
                        "landed_lf_sha256": forms["landed-lf"],
                        "content_sha256_form_matched": matched_form,
                        "frozen_content_sha256": entry["content_sha256"],
                        "matches": matched_form is not None})
        if matched_form is None:
            problems.append(
                f"manifest drift {entry['path']}: {forms['exact-bytes'][:12]}"
                f" (lf {forms['landed-lf'][:12]}) != "
                f"{entry['content_sha256'][:12]}")
    if problems:
        raise BindingRefusal(
            "IMPLEMENTATION_MANIFEST_DRIFT", "; ".join(problems))
    digest = implementation_manifest_digest(freeze["implementation_manifest"])
    if digest != freeze["implementation_manifest_sha256"]:
        raise BindingRefusal(
            "IMPLEMENTATION_MANIFEST_DRIFT",
            f"manifest digest {digest} != frozen "
            f"{freeze['implementation_manifest_sha256']}")
    return {"implementation_manifest_sha256": digest,
            "files_checked": checked}


# ---------------------------------------------------------------------------
# Persisted provider-artifact parsing (pure text)
# ---------------------------------------------------------------------------

def _agent_message_from_raw(raw_path: Path):
    big = _load_big()
    stdout = raw_path.read_text(encoding="utf-8", errors="replace")
    return big.extract_agent_message(stdout)


def _last_json_from_raw(raw_path: Path) -> dict:
    big = _load_big()
    msg = _agent_message_from_raw(raw_path)
    if msg is None:
        raise BindingRefusal(
            "RECONSTRUCTION_ARTIFACT_INCOMPLETE",
            f"no agent_message event in {raw_path.name}")
    return big.extract_json_object(msg)


def load_grouping_payload(run_root: Path) -> dict:
    """Final grouping call output (completeness retry supersedes first)."""
    root = Path(run_root) / "grouping"
    for name in ("grouping-completeness.raw.jsonl", "grouping.raw.jsonl"):
        p = root / name
        if p.exists():
            return _last_json_from_raw(p)
    raise BindingRefusal(
        "RECONSTRUCTION_ARTIFACT_INCOMPLETE",
        f"no grouping raw capture under {root}")


def load_relations_wrapper(run_root: Path) -> dict:
    p = Path(run_root) / "relations" / "prompts" / "relations.raw.jsonl"
    if not p.exists():
        raise BindingRefusal(
            "RECONSTRUCTION_ARTIFACT_INCOMPLETE",
            f"missing {p}")
    return _last_json_from_raw(p)


# ---------------------------------------------------------------------------
# DETERMINISTIC_ASSEMBLY_REPLAY_V1
# ---------------------------------------------------------------------------

def reconstruct_contestant(run_root, freeze: dict) -> tuple[dict, dict]:
    """Rebuild one contestant's final payload WITHOUT any provider call.

    Returns (assembled_payload, replay_stats). Raises BindingRefusal on
    artifact inconsistency; raises the strict validator's
    InferenceContractError unchanged if the rebuilt payload fails the
    frozen gate (that is a reconstruction failure, not a refusal code).
    """
    from ef import contract_v2 as v2  # pure functions only
    big = _load_big()
    run_root = Path(run_root)
    plan = json.loads((run_root / "plan.json").read_text(
        encoding="utf-8"))
    if plan.get("plan_id") != \
            freeze["runtime_configuration"]["FROZEN_PLAN_ID"]:
        raise BindingRefusal(
            "RECONSTRUCTION_PLAN_MISMATCH",
            f"{run_root.name}: plan_id {plan.get('plan_id')!r} != frozen")
    eligible = list(plan["eligible_cluster_ids"])

    inventory: list[dict] = []
    phase1_consistent = True
    for b in plan["batches"]:
        vj = run_root / "phase1" / f"{b['batch_id']}-phase1.validated.json"
        if not vj.exists():
            raise BindingRefusal(
                "RECONSTRUCTION_ARTIFACT_INCOMPLETE", f"missing {vj}")
        doc = json.loads(vj.read_text(encoding="utf-8"))
        supplied = sorted(int(c) for c in b["cluster_ids"])
        valid, failures = v2.validate_phase1_payload(
            doc.get("payload"), supplied)
        if failures != doc.get("failures"):
            phase1_consistent = False
        inventory.extend(v2.build_inventory(valid, b["batch_id"]))

    groups_payload = load_grouping_payload(run_root)
    groups = groups_payload.get("groups")
    if not isinstance(groups, list):
        raise BindingRefusal(
            "RECONSTRUCTION_ARTIFACT_INCOMPLETE",
            f"{run_root.name}: grouping payload has no groups array")
    ok, _, probs = v2.verify_group_coverage(groups, inventory)
    if not ok:
        raise BindingRefusal(
            "RECONSTRUCTION_GROUPING_COVERAGE",
            f"{run_root.name}: {probs[:3]}")
    canon, disps = v2.assemble_canonical(groups, inventory, eligible)

    rel_inventory = [{"id": c["canonical_id"], "batch_id": "",
                      "type": c["type"],
                      "name": c["object"].get("name"),
                      "text": c["object"].get("text"),
                      "topic": c["object"].get("topic"),
                      "cluster_ids": c["provenance_cluster_ids"],
                      "object": c["object"]}
                     for c in canon]
    wrapper = load_relations_wrapper(run_root)
    ints = [o["id"] for o in rel_inventory if o["type"] == "interest"]
    qs = [o["id"] for o in rel_inventory if o["type"] == "question"]
    rgs = [o["id"] for o in rel_inventory if o["type"] == "regret"]
    accepted, quarantine = v2.verify_relations(wrapper, ints, qs, rgs)

    extra_disps: list = []
    assembled, receipts = v2.apply_relations_to_assembly(
        canon, accepted, extra_disps)
    big.validate_inference(assembled, set(eligible))

    stats = {
        "run_root": str(run_root),
        "procedure": RECONSTRUCTION_PROCEDURE,
        "objects_in": len(inventory),
        "canonical_objects": len(canon),
        "explicit_dispositions": len(disps) + len(extra_disps),
        "assemble_canonical_dispositions": len(disps),
        "relation_stage_dispositions": len(extra_disps),
        "quarantined_edges": len(quarantine),
        "required_link_failures": len(receipts["required_link_failures"]),
        "phase1_failures_consistent": phase1_consistent,
        "strict_validator": "PASSED validate_inference "
                            "(build_interest_graph, frozen manifest blob)",
    }
    if not phase1_consistent:
        raise BindingRefusal(
            "RECONSTRUCTION_ARTIFACT_INCONSISTENT",
            f"{run_root.name}: re-validated phase-1 failures differ from "
            "the persisted batch receipts")
    return assembled, stats


# ---------------------------------------------------------------------------
# Binding verification / manifest
# ---------------------------------------------------------------------------

def binding_identity(contestant_bindings: list[dict]) -> str:
    """Order-invariant digest of the bound contestant identity set."""
    normalized = sorted(
        ({"run_id": c["run_id"],
          "expected_sha256": c["expected_sha256"],
          "reconstructed_sha256": c["reconstructed_sha256"]}
         for c in contestant_bindings),
        key=lambda d: d["run_id"])
    return sha256_bytes(json.dumps(
        normalized, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def verify_binding(repo_root, freeze_path, expected_contestants=None) -> dict:
    """Full pre-unseal binding verification over the three contestants.

    expected_contestants: optional {run_id: sha256} override used by the
    synthetic refusal tests; defaults to the freeze document itself.
    """
    freeze = load_freeze(freeze_path)
    manifest_receipt = verify_implementation_manifest(repo_root, freeze)
    expected = expected_contestants or {
        cid: freeze["contestant_outputs"][cid]["payload_canonical_sha256"]
        for cid in BOUND_CONTESTANT_IDS}
    if sorted(expected) != sorted(BOUND_CONTESTANT_IDS):
        raise BindingRefusal(
            "CONTESTANT_SET_MISMATCH",
            f"expected set {sorted(expected)} != three bound contestants")

    contestants = []
    recipe = None
    for cid in BOUND_CONTESTANT_IDS:
        entry = freeze["contestant_outputs"][cid]
        run_root = Path(entry["run_root"])
        if not run_root.exists():
            raise BindingRefusal(
                "CONTESTANT_RUN_ROOT_MISSING",
                f"{cid}: {run_root} does not exist")
        assembled, stats = reconstruct_contestant(run_root, freeze)
        variants = canonical_serialization_variants(assembled)
        expected_hash = expected[cid]
        matches = {name: sha256_bytes(blob) == expected_hash
                   for name, blob in variants.items()}
        if recipe is None:
            recipe = {name for name, hit in matches.items() if hit}
        else:
            recipe = recipe & {name for name, hit in matches.items()
                               if hit}
        contestants.append({
            "run_id": cid,
            "run_root": str(run_root),
            "expected_sha256": expected_hash,
            "reconstructed_sha256": stats and next(
                (sha256_bytes(blob) for name, blob in variants.items()
                 if name == "json.dumps(sort_keys)"), ""),
            "reconstructed_valid_sha256s": {
                name: sha256_bytes(blob) for name, blob in
                variants.items()},
            "byte_exact": any(matches.values()),
            "strict_validator": stats["strict_validator"],
            "counts": {k: stats[k] for k in (
                "objects_in", "canonical_objects",
                "explicit_dispositions", "quarantined_edges",
                "required_link_failures")},
        })

    if not recipe:
        raise BindingRefusal(
            "CONTESTANT_RECONSTRUCTION_MISMATCH",
            "no canonical serialization reproduces the bound hashes; "
            "STOP — do not generate replacement runs")
    chosen = sorted(recipe)[0]
    for c in contestants:
        if not c["byte_exact"]:
            raise BindingRefusal(
                "CONTESTANT_RECONSTRUCTION_MISMATCH",
                f"{c['run_id']}: reconstructed payload does not match "
                f"the bound hash {c['expected_sha256']}")
        c["reconstructed_sha256"] = \
            c["reconstructed_valid_sha256s"][chosen]

    return {
        "binding_status": BINDING_STATUS,
        "reconstruction": {
            "procedure": RECONSTRUCTION_PROCEDURE,
            "serialization_recipe": chosen,
            "provider_calls": "ZERO — pure replay of persisted artifacts",
            "holdout_opened": "NO",
        },
        "inference_freeze": {
            "freeze_commit": None,  # filled by caller (git-known)
            "implementation_manifest_sha256":
                manifest_receipt["implementation_manifest_sha256"],
            "runtime_configuration":
                freeze["runtime_configuration"],
        },
        "contestants": contestants,
        "binding_identity_sha256": binding_identity(contestants),
    }
