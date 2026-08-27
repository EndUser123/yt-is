"""CONTRACT ARCHITECTURE GENERATION v2 — decomposed-arm bakeoff driver.

Executes arms D1 / D2 / D3 per the FROZEN protocol in
docs/handoffs/interest-intelligence/contract-architecture-v2-
preregistration.md. D0 is cited from bakeoff-1 artifacts, never rerun.

Provider execution measurement utilities are imported from
scripts/interest_contract_bakeoff.py (capture/parse/retry single-source).

Usage:
    python scripts/contract_v2_bakeoff.py --arm D1 [--artifact-dir DIR]
    python scripts/contract_v2_bakeoff.py --report DIR
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(          # noqa: E402
    "build_interest_graph", REPO / "scripts" / "build_interest_graph.py")
big = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(big)

_ibspec = importlib.util.spec_from_file_location(        # noqa: E402
    "interest_contract_bakeoff",
    REPO / "scripts" / "interest_contract_bakeoff.py")
ib = importlib.util.module_from_spec(_ibspec)
_ibspec.loader.exec_module(ib)

from ef import contract_v2 as v2                          # noqa: E402
from ef.inference_contract import (conformance_errors,    # noqa: E402
                                   grouping_output_schema,
                                   inference_output_schema,
                                   phase1_output_schema,
                                   relation_output_schema)

TIMEOUT_S = ib.TIMEOUT_S
FROZEN_PLAN_ID = "plan_01b09359b3f05784"


def _retryable(cap) -> bool:
    return cap["returncode"] != 0 or cap["timed_out"] \
        or cap["text"] is None


def call_provider(prompt_file: Path, schema_file: Path | None,
                  raw_out: Path | None):
    """Single-attempt provider call with ONE exec-class retry."""
    cap = ib.run_codex_capture(prompt_file, schema_file, raw_out=raw_out)
    usage = cap["usage"]
    latency = cap["latency_s"]
    retries = 0
    if _retryable(cap):
        retries = 1
        cap = ib.run_codex_capture(prompt_file, schema_file,
                                   raw_out=raw_out)
        latency += cap["latency_s"]
        for k in usage:
            usage[k] += cap["usage"].get(k, 0)
    return cap, {"latency_s": round(latency, 3), "retries": retries,
                 "usage": usage}


def _call_json(prompt_text: Path | str, work_dir: Path, tag: str,
               schema_file: Path | None, ledger: dict):
    if isinstance(prompt_text, str):
        pf = work_dir / f"{tag}.txt"
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(prompt_text, encoding="utf-8")
        prompt_file = pf
    else:
        prompt_file = prompt_text
    cap, meter = call_provider(
        prompt_file, schema_file,
        raw_out=work_dir / f"{tag}.raw.jsonl")
    ledger["calls"] += 1
    ledger["latency_s"] += meter["latency_s"]
    ledger["retries"] += meter["retries"]
    for k in ("input_tokens", "output_tokens", "cached_input_tokens"):
        ledger["tokens"][k] += meter["usage"].get(k, 0)
    payload_or_err = ib._safe_extract(cap)
    if isinstance(payload_or_err, big.ProviderExecutionError):
        ledger["provider_exec_or_parse_failures"] += 1
        return None
    return payload_or_err


def new_ledger() -> dict:
    return {"calls": 0, "latency_s": 0.0, "retries": 0,
            "provider_exec_or_parse_failures": 0,
            "tokens": {"input_tokens": 0, "output_tokens": 0,
                       "cached_input_tokens": 0}}


# ---------------------------------------------------------------------------
# phase 1
# ---------------------------------------------------------------------------

def run_phase1(arm: str, plan, hydrated, work_dir: Path,
               semantic_head: str, schema_path: Path):
    ledger = new_ledger()
    m = {"batches_complete": 0, "valid_object_payloads": 0,
         "valid_interest_objects": 0, "invalid_core_objects": 0,
         "invalid_optional_objects": 0, "schema_envelope_failures": 0,
         "schema_item_failures": 0, "semantic_item_failures": 0}
    inventory: list[dict] = []
    rows = []
    work_dir.mkdir(parents=True, exist_ok=True)
    for b in plan.batches:
        tag = f"{b.batch_id}-phase1"
        cluster_rows = hydrated[b.batch_id]
        packets = big.build_prompt(cluster_rows)
        supplied = sorted(int(c["cluster_id"]) for c in cluster_rows)
        prompt = v2.render_phase1_prompt(
            semantic_head, min(len(cluster_rows), 25), packets)
        payload = _call_json(prompt, work_dir / "prompts", tag,
                             schema_path, ledger)
        row = {"batch_id": b.batch_id, "status": None, "calls": 0,
               "invalid_core": 0, "invalid_optional": 0,
               "valid_interests": 0}
        if payload is None:
            row["status"] = "provider_failure"
            rows.append(row)
            continue
        try:
            valid, failures = v2.validate_phase1_payload(payload, supplied)
        except ValueError as exc:
            row["status"] = "envelope_violation"
            m["schema_envelope_failures"] += 1
            row["detail"] = str(exc)[:300]
            rows.append(row)
            continue
        core_fails = [f for f in failures if f["severity"] == "core"]
        opt_fails = [f for f in failures if f["severity"] == "optional"]
        m["schema_item_failures"] += sum(
            1 for f in failures if f["error_class"] == "schema_enforcement")
        m["semantic_item_failures"] += sum(
            1 for f in failures if f["error_class"] == "semantic")
        m["invalid_core_objects"] += len(core_fails)
        m["invalid_optional_objects"] += len(opt_fails)
        if arm == "D1" and failures:
            row.update({"status": "item_defect_strict_fail",
                        "invalid_core": len(core_fails),
                        "invalid_optional": len(opt_fails)})
            rows.append(row)
            continue
        if not valid["inferred_interests"]:
            row["status"] = "no_valid_core_interests"
            rows.append(row)
            continue
        inv = v2.build_inventory(valid, b.batch_id)
        inventory.extend(inv)
        m["batches_complete"] += 1
        m["valid_object_payloads"] += 1
        m["valid_interest_objects"] += len(valid["inferred_interests"])
        row.update({"status": "complete",
                    "valid_interests": len(valid["inferred_interests"]),
                    "invalid_core": len(core_fails),
                    "invalid_optional": len(opt_fails)})
        ib._write_json(work_dir / f"{tag}.validated.json",
                        {"payload": payload, "failures": failures})
        rows.append(row)
        print(f"[v2:{arm}] {b.batch_id} phase1: {row['status']} "
              f"(interests {row['valid_interests']}, "
              f"bad-core {row['invalid_core']}, "
              f"bad-opt {row['invalid_optional']})")
    return {"ledger": ledger, "metrics": m, "inventory": inventory,
            "rows": rows}


# ---------------------------------------------------------------------------
# relation stage
# ---------------------------------------------------------------------------

RELATION_REPAIR_PROMPT = """Your previous relationship proposal referenced ids outside this authoritative set.

VALID INTEREST IDS:
{interest_ids}

VALID QUESTION IDS:
{question_ids}

VALID REGRET IDS:
{regret_ids}

Return a corrected COMPLETE proposal with the same four arrays; every referenced id MUST come from these sets. Remove invalid references rather than guessing replacements. Do NOT invent ids."""


def run_relations(arm: str, inventory, work_dir: Path,
                  schema_path: Path, eligible_ids):
    led = new_ledger()
    m = {"calls": 0, "valid_edges": 0, "invalid_endpoints": 0,
         "quarantined_optional_edges": 0, "required_link_failures": 0,
         "repairs": 0}
    ints = [o["id"] for o in inventory if o["type"] == "interest"]
    qs = [o["id"] for o in inventory if o["type"] == "question"]
    rgs = [o["id"] for o in inventory if o["type"] == "regret"]
    wrapper = _call_json(v2.render_relation_prompt(inventory),
                         work_dir / "prompts", "relations", schema_path,
                         led)
    if wrapper is None:
        m["calls"] = led["calls"]
        return {"ok": False, "reason": "provider_failure", **m}, None, None
    accepted, quarantine = v2.verify_relations(wrapper, ints, qs, rgs)

    def total_endpoint_bad(w):
        bad = 0
        bad += sum(1 for e in (w.get("related_edges") or [])
                   if not isinstance(e, dict) or
                   e.get("source_id") not in ints or
                   e.get("target_id") not in ints)
        bad += sum(1 for e in (w.get("parent_edges") or [])
                   if not isinstance(e, dict) or
                   e.get("child_id") not in ints or
                   e.get("parent_id") not in ints)
        bad += sum(1 for e in (w.get("question_links") or [])
                   if not isinstance(e, dict) or
                   e.get("question_id") not in qs or
                   e.get("interest_id") not in ints)
        bad += sum(1 for e in (w.get("regret_links") or [])
                   if not isinstance(e, dict) or
                   e.get("regret_id") not in rgs or
                   e.get("interest_id") not in ints)
        return bad

    endpoint_bad = total_endpoint_bad(wrapper)
    if arm == "D1" and endpoint_bad:
        # ONE bounded repair against explicit valid-id lists
        m["repairs"] = 1
        fix_prompt = RELATION_REPAIR_PROMPT.format(
            interest_ids="\n".join(ints), question_ids="\n".join(qs),
            regret_ids="\n".join(rgs))
        fixed = _call_json(fix_prompt, work_dir / "prompts",
                           "relations-repair", schema_path, led)
        if fixed is not None:
            accepted, quarantine = v2.verify_relations(
                fixed, ints, qs, rgs)
            endpoint_bad = total_endpoint_bad(fixed)
    m["invalid_endpoints"] = endpoint_bad
    if arm == "D1" and endpoint_bad:
        m["calls"] = led["calls"]
        return {"ok": False, "reason": "endpoint_violations_strict",
                **m}, None, None

    # required links: every canonical question needs an accepted edge
    # else receipted quarantines; optional quarantine tallies are direct.
    linked_q, required_failures = [], []
    qlink = {e["question_id"]: e["interest_id"]
             for e in accepted["question_links"]}
    for oid in qs:
        if oid not in qlink:
            required_failures.append({"source_id": oid})
    m["quarantined_optional_edges"] = len(quarantine)
    m["valid_edges"] = sum(len(v) for v in accepted.values())
    m["required_link_failures_pending_verification"] = len(required_failures)
    m["calls"] = led["calls"]
    return {"ok": True, **m}, accepted, {"int_ids": ints, "q_ids": qs,
                                         "rg_ids": rgs,
                                         "quarantine": quarantine,
                                         "required_failures":
                                             required_failures}


# ---------------------------------------------------------------------------
# reconciliation D1/D2 (monolithic tree + sanitation R-1)
# ---------------------------------------------------------------------------

def make_prose_recon_adapter(ledger: dict):
    """Old-template reconciliation calls: measurement adapter mirroring
    bakeoff-1's arm-A invocation exactly (no schema attachment)."""
    seq = [0]

    def adapter(provider, prompt, prompt_file, timeout):
        del timeout
        seq[0] += 1
        own = prompt_file.parent / (
            f"{prompt_file.stem}-call{seq[0]:03d}.txt")
        own.write_text(prompt, encoding="utf-8")
        cap, meter = call_provider(own, None,
                                   raw_out=own.parent /
                                   f"{own.stem}.raw.jsonl")
        ledger["calls"] += 1
        ledger["latency_s"] += meter["latency_s"]
        ledger["retries"] += meter["retries"]
        for k in ("input_tokens", "output_tokens",
                  "cached_input_tokens"):
            ledger["tokens"][k] += meter["usage"].get(k, 0)
        parsed = ib._extract_payload(cap)
        return parsed, ib.MODEL

    return adapter


def sanitize_and_validate(tree_final, leaf_dispositions,
                          leafid_to_source_name, accepted_edges,
                          name_by_object_id, eligible_ids,
                          final_invoke=None):
    """R-1 sanitizer.

    Reconciliation-emitted relational fields are DISCARDED WHOLESALE and
    replaced by mechanically translated accepted edges: every leaf
    fragment is mapped through the tree's own flattened dispositions to
    its surviving final-interest name(s), and each leaf contributes its
    pre-recon parent/related_to (computed mechanically during relation
    attach). Translation targets that no longer resolve are recorded as
    DROPPED_TRANSLATION receipts (review finding F5). Reference-class
    residue gets the sanctioned bounded repair ONCE via ``final_invoke``
    when provided; anything else fails closed via raise.
    """
    dropped_translations: list[dict] = []
    final = copy.deepcopy(tree_final)
    int_names_norm = {big._norm_name(i["name"])
                      for i in final["inferred_interests"]}

    # mechanical pre-recon relations by original source name
    parent_by_src = {}
    related_by_src = {}
    ints_ids = set()
    for e in accepted_edges["parent_edges"]:
        src = name_by_object_id.get(e["child_id"])
        tgt = name_by_object_id.get(e["parent_id"])
        if src and tgt:
            parent_by_src[big._norm_name(src)] = tgt
            ints_ids.add(e["child_id"])
    for e in accepted_edges["related_edges"]:
        s = name_by_object_id.get(e["source_id"])
        t = name_by_object_id.get(e["target_id"])
        if s and t:
            related_by_src.setdefault(big._norm_name(s),
                                      set()).add(t)
            ints_ids.add(e["source_id"])

    translated_parent = {}
    translated_related = {}
    for fid, src_name in leafid_to_source_name.items():
        d = next((x for x in leaf_dispositions
                  if x["fragment_id"] == fid), None)
        if d is None:
            continue
        tgt_names = []
        if d["decision"] == "discarded":
            continue
        tn = d.get("target_interest")
        if tn is None:
            continue
        tnorm = big._norm_name(tn)
        if tnorm not in int_names_norm:
            dropped_translations.append({
                "fragment_id": fid, "target_interest": tn})
            continue
        tgt_names.append(tnorm)
        key = big._norm_name(src_name)
        for tnorm in tgt_names:
            if key in parent_by_src:
                translated_parent[tnorm] = parent_by_src[key]
            translated_related.setdefault(tnorm, set()).update(
                related_by_src.get(key, set()))

    for it in final["inferred_interests"]:
        nm = big._norm_name(it["name"])
        if nm in translated_parent or True:
            # wholesale replacement: only translator output survives
            it["parent"] = translated_parent.get(nm)
            it["related_to"] = sorted(
                translated_related.get(nm, set()))

    # Mechanical duplicate-name containment (falsifier: duplicate
    # semantic objects across batches). If the monolithic recon leaves
    # identical-key interests unmerged, deterministic repair happens HERE
    # with receipted counts — never silent.
    dedup_merges = 0
    buckets: dict[str, list] = {}
    for i, it in enumerate(final["inferred_interests"]):
        buckets.setdefault(big._norm_name(it["name"]), []).append(i)
    kept_interests = []
    for key in sorted(buckets):
        idxs = buckets[key]
        ordered = sorted(idxs, key=lambda i: (
            -float(final["inferred_interests"][i].get("confidence", 0)),
            i))
        survivor = copy.deepcopy(final["inferred_interests"][ordered[0]])
        if len(ordered) > 1:
            cids = sorted({c for i in ordered
                           for c in final["inferred_interests"][i]
                           ["cluster_ids"]})
            survivor["cluster_ids"] = cids
            dedup_merges += len(ordered) - 1
        kept_interests.append(survivor)
    final["inferred_interests"] = kept_interests

    try:
        big.validate_inference(final, set(eligible_ids))
    except big.InferenceContractError as exc:
        # sanctioned ONE-SHOT reference repair for translation residue
        if big.classify_contract_error(exc) != "reference" or \
                final_invoke is None:
            raise
        fixed, _, used = big.validated_reference_repair(
            final, set(eligible_ids), final_invoke)
        dedup_merges += 0
        status_extra = f"+reference_repair_once({used})"
        final = fixed
        status = "strict_pass_after_R1_translation" + \
            (f"+mechanical_dupe_merge({dedup_merges})"
             if dedup_merges else "") + status_extra
        if dropped_translations:
            status += (f"+dropped_translations("
                       f"{len(dropped_translations)})")
        return final, {"status": status,
                       "dropped_translations": dropped_translations}
    status = "strict_pass_after_R1_translation"
    if dedup_merges:
        status += f"+mechanical_dupe_merge({dedup_merges})"
    if dropped_translations:
        status += (f"+dropped_translations({len(dropped_translations)})"
                   ", recorded in receipts")
    return final, {"status": status,
                   "dropped_translations": dropped_translations}


_eligible_cache = {}


def eligible_all():
    return _eligible_cache["ids"]


# ---------------------------------------------------------------------------
# arm runners
# ---------------------------------------------------------------------------

def write_schemas(root: Path) -> dict:
    out = root / "schemas"
    ib._write_json(out / "phase1-output-schema.json",
                    phase1_output_schema())
    ib._write_json(out / "relation-output-schema.json",
                    relation_output_schema())
    ib._write_json(out / "grouping-output-schema.json",
                    grouping_output_schema())
    # v1-shaped schema: attached to post-recon one-shot reference repairs
    ib._write_json(out / "inference-output-schema.json",
                    inference_output_schema())
    return {"phase1": out / "phase1-output-schema.json",
            "relation": out / "relation-output-schema.json",
            "grouping": out / "grouping-output-schema.json",
            "inference": out / "inference-output-schema.json"}


GROUPING_PROMPT_TEMPLATE = """You are reconciling independent VALIDATED semantic objects produced by separate batches analysing one person's evidence corpus. Objects may repeat across batches under slightly different wording. Your task is ONLY equivalence GROUPING — never rewriting.

OBJECT INVENTORY (JSON):

{inventory}

Assign EVERY id above into exactly one group:
- action "distinct": the object stands alone.
- action "merged": members describe the SAME underlying interest/question/regret (repeat duplicates, trivial rewordings). Provide a short reason.
- action "drop_noise": ONLY for questions/regret candidates that are meaningless noise; give a reason. NEVER drop interests.
Do NOT merge different interests merely because they share broad topics; when unsure use distinct.

Return ONLY valid JSON"""


def render_grouping_prompt(inventory) -> str:
    rows = []
    for o in inventory:
        obj = o["object"]
        label = obj.get("name") or obj.get("text") or obj.get("topic")
        ev = (obj.get("evidence_summary") or "")[:60]
        rows.append({"id": o["id"], "t": o["type"], "label": label,
                     "conf": obj.get("confidence"), "clusters":
                         o["cluster_ids"][:6], "ev": ev})
    return GROUPING_PROMPT_TEMPLATE.format(
        inventory=json.dumps(rows, indent=0, ensure_ascii=False))


def run_arm(arm: str, artifact_dir: str | None) -> int:
    from csf.paths import load_workspace_env
    load_workspace_env()
    from ef.evidence_clusters import evidence_cluster_inventory, \
        hydrate_evidence_clusters
    from ef.interest_candidates import CandidatePlan, \
        build_bootstrap_plan, validate_plan_coverage

    inventory_db = evidence_cluster_inventory()
    plan = build_bootstrap_plan(
        inventory_db["clusters"],
        max_per_call=big.BOOTSTRAP_MAX_CLUSTERS_PER_CALL,
        exclusions=inventory_db.get("exclusions", {}))
    validate_plan_coverage(plan, big.BOOTSTRAP_MAX_CLUSTERS_PER_CALL)
    # FROZEN_PLAN_ID guard (review finding F6): the measured universe
    # must be the exact frozen deterministic plan; any drift aborts
    # BEFORE the first provider call.
    if plan.plan_id != FROZEN_PLAN_ID:
        raise SystemExit(
            f"FROZEN PLAN MISMATCH: live inventory produced "
            f"{plan.plan_id}, expected {FROZEN_PLAN_ID}; refusing to "
            "measure against a drifted corpus")
    _eligible_cache["ids"] = list(plan.eligible_cluster_ids)

    stamp = time.strftime("%Y%m%dT%H%M%S")
    uid = uuid.uuid4().hex[:8]
    root = Path(artifact_dir) if artifact_dir else (
        big.ARTIFACT_ROOT /
        f"bakeoff-v2-{stamp}_{uid}_{arm}")
    root.mkdir(parents=True, exist_ok=True)
    schemas = write_schemas(root)
    prereg = REPO / ("docs/handoffs/interest-intelligence/"
                     "contract-architecture-v2-preregistration.md")
    ib._write_json(root / "preregistration.json", {
        "path": str(prereg),
        "sha256": hashlib.sha256(prereg.read_bytes()).hexdigest()})
    ib._write_json(root / "plan.json", plan.to_dict())

    semantic_head = big.PROMPT_TEMPLATE.split("Return ONLY")[0].rstrip()
    hydrated = {b.batch_id: hydrate_evidence_clusters(list(b.cluster_ids))
                for b in plan.batches}

    t0 = time.monotonic()
    p1 = run_phase1(arm, plan, hydrated, root / "phase1", semantic_head,
                    schemas["phase1"])
    print(f"[v2:{arm}] phase1 complete batches "
          f"{p1['metrics']['batches_complete']}/13")

    # ---------------- relations ----------------
    rel_work = root / "relations"
    if arm == "D3":
        # D3 groups first over raw inventory (no relations yet), then a
        # SINGLE relation pass over the canonical set.
        gr_led = new_ledger()
        groups_payload = _call_json(
            render_grouping_prompt(p1["inventory"]),
            root / "grouping", "grouping", schemas["grouping"], gr_led)
        retries_used = 0
        if groups_payload is not None:
            groups = groups_payload.get("groups")
            ok, _, probs = v2.verify_group_coverage(groups or [],
                                                    p1["inventory"])
            if not ok:
                retries_used = 1
                fix_prompt = render_grouping_prompt(
                    [o for o in p1["inventory"]]) + \
                    "\n\nMANDATORY: your previous attempt violated " \
                    "coverage:\n" + "\n".join(probs[:5])
                retry = _call_json(fix_prompt, root / "grouping",
                                   "grouping-completeness",
                                   schemas["grouping"], gr_led)
                if retry is not None:
                    groups_payload = retry
            ok, _, probs = v2.verify_group_coverage(
                groups_payload.get("groups") or [], p1["inventory"])
            if not ok:
                print(f"[v2:{arm}] grouping coverage FAILED: {probs[:3]}")
                return _finish(root, arm,
                               {"completed": False,
                                "why": f"grouping_omission: {probs[:2]}"},
                               gr_led, None, t0)
        if groups_payload is None:
            return _finish(root, arm, {"completed": False,
                                       "why": "grouping_provider"},
                           gr_led, None, t0)
        canon, disps = v2.assemble_canonical(groups_payload["groups"],
                                             p1["inventory"],
                                             list(plan.eligible_cluster_ids))
        rel_inventory = [{"id": c["canonical_id"], "batch_id": "",
                          "type": c["type"],
                          "name": c["object"].get("name"),
                          "text": c["object"].get("text"),
                          "topic": c["object"].get("topic"),
                          "cluster_ids":
                              c["provenance_cluster_ids"],
                          "object": c["object"]}
                         for c in canon]
        rel = run_relations(arm, rel_inventory, root / "relations",
                            schemas["relation"],
                            list(plan.eligible_cluster_ids))
        if not rel[0]["ok"]:
            return _finish(root, arm, {"completed": False,
                                       "why": f"relations_{rel[0]}" },
                           gr_led, rel, t0)
        accepted = rel[1]
        extra_disps: list = []
        assembled, receipts = v2.apply_relations_to_assembly(
            canon, accepted, extra_disps)
        big.validate_inference(assembled,
                               set(plan.eligible_cluster_ids))
        result = {"completed": True, "mode": "decomposed_reconciliation",
                  "objects_in": len(p1["inventory"]),
                  "explicit_dispositions": len(disps) +
                  len(extra_disps),
                  "canonical_objects": len(canon),
                  "required_link_receipts": receipts}
        return _finish(root, arm, result, gr_led, rel, t0,
                       extra={"phase1": p1["metrics"], "phase1_rows": p1["rows"],
                              "assembled_counts": {
                                  k: len(v) for k, v
                                  in assembled.items()}})

    rel = run_relations(arm, p1["inventory"], rel_work,
                        schemas["relation"], list(plan.eligible_cluster_ids))
    rel_metrics, accepted, meta = rel
    if not rel_metrics["ok"]:
        return _finish(root, arm, {"completed": False,
                                   "why": rel_metrics},
                       p1["ledger"], None, t0,
                       extra={"phase1": p1["metrics"], "phase1_rows": p1["rows"],
                              "relations": rel_metrics})

    # ---------------- monolithic reconciliation (D1/D2) ----------------
    fragments = {"interests": [], "questions": [], "regret_candidates": []}
    qname = {}
    rname = {}
    by_id = {o["id"]: o for o in p1["inventory"]}
    for e in accepted["question_links"]:
        q = by_id.get(e["question_id"])
        i = by_id.get(e["interest_id"])
        if q and i:
            qd = dict(q["object"])
            qd["interest"] = i["object"]["name"]
            fragments["questions"].append(qd)
            qname[e["question_id"]] = qd
    for e in accepted["regret_links"]:
        r = by_id.get(e["regret_id"])
        i = by_id.get(e["interest_id"])
        rd = dict(r["object"]) if r else None
        if rd is not None and i:
            rd["related_interests"] = [i["object"]["name"]]
            fragments["regret_candidates"].append(rd)
            rname[e["regret_id"]] = rd
    name_lookup = {o["id"]: o["object"]["name"]
                   for o in p1["inventory"] if o["type"] == "interest"}
    parent_by_child, related_sets = {}, {}
    for e in accepted["parent_edges"]:
        parent_by_child[e["child_id"]] = e["parent_id"]
    for e in accepted["related_edges"]:
        related_sets.setdefault(e["source_id"], set()).add(
            e["target_id"])
    recon_ledger = new_ledger()
    leaf_rows = []
    for o in p1["inventory"]:
        if o["type"] != "interest":
            continue
        it = dict(o["object"])
        pid = parent_by_child.get(o["id"])
        it["parent"] = name_lookup.get(pid) if pid else None
        it["related_to"] = sorted(name_lookup.get(tid, "")
                                  for tid in
                                  related_sets.get(o["id"], set()))
        it["related_to"] = [x for x in it["related_to"] if x]
        fid = "frag_" + o["id"]
        fragments["interests"].append({
            "fragment_id": fid, "batch_id": o["batch_id"],
            "interest": it, "cluster_ids": list(o["cluster_ids"])})
        leaf_rows.append((fid, o))

    t1 = time.monotonic()
    try:
        tree = big.run_reconciliation_tree(
            fragments, list(plan.eligible_cluster_ids), provider="codex",
            timeout=TIMEOUT_S,
            prompt_path=root / "reconciliation" / "tree-group.txt",
            invoke=make_prose_recon_adapter(recon_ledger),
            stage_writer=lambda s, rec_, base=root / "reconciliation":
            ib._write_json(base / f"monolith-stage-{s:02d}.json", rec_))
    except Exception as exc:
        # genuine provider/recon contract defect OR unexpected harness
        # error -> receipted arm result either way (review minor folded
        # into AMENDMENT 2: flake and ReconciliationContractError classes
        # included). Classified by type name for taxonomy honesty.
        return _finish(root, arm,
                       {"completed": False,
                        "why": f"{type(exc).__name__}: {exc}"[:400]},
                       recon_ledger, rel, t0,
                       extra={"phase1": p1["metrics"],
                              "phase1_rows": p1["rows"],
                              "relations": rel_metrics,
                              "recon_ledger": recon_ledger})

    # ---- R-1 sanitation + strict gate ----
    leafid_to_source_name = {
        fid: orig["object"]["name"] for fid, orig in leaf_rows}
    repair_seq = [0]

    def _post_recon_invoke(prompt):
        """Sanctioned one-shot reference repair (functional closure).

        Writes the prompt to a uniquely numbered file, attaches the v1
        inference output schema (the repaired payload is v1-shaped), and
        reuses the standard capture/retry path.
        """
        repair_seq[0] += 1
        p = (root / "reconciliation" /
             f"post-recon-reference-repair-{repair_seq[0]}.txt")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(prompt, encoding="utf-8")
        cap, meter = call_provider(
            p, schemas["inference"],
            raw_out=p.parent / f"{p.stem}.raw.jsonl")
        recon_ledger["calls"] += 1
        recon_ledger["latency_s"] += meter["latency_s"]
        recon_ledger["retries"] += meter["retries"]
        for k in recon_ledger["tokens"]:
            recon_ledger["tokens"][k] += meter["usage"].get(k, 0)
        return ib._extract_payload(cap)

    try:
        assembled, sani = sanitize_and_validate(
            tree["final"], tree["fragment_dispositions"],
            leafid_to_source_name, accepted, name_lookup,
            list(plan.eligible_cluster_ids),
            final_invoke=_post_recon_invoke)
        sanitized_status = sani["status"]
        ib._write_json(root / "reconciliation" / "r1-receipts.json", sani)
    except big.InferenceContractError as exc:
        return _finish(root, arm, {"completed": False,
                                   "why":
                                   f"sanitation_gate_{type(exc).__name__}:"
                                   f" {str(exc)[:300]}"},
                       recon_ledger, rel, t0,
                       extra={"phase1": p1["metrics"],
                              "phase1_rows": p1["rows"],
                              "relations": rel_metrics,
                              "recon_ledger": recon_ledger})
    except big.ProviderExecutionError as exc:
        # provider flake INSIDE the sanctioned repair: fail closed with
        # receipts, never crash the arm (review blocker run-b39505889cfe)
        return _finish(root, arm,
                       {"completed": False,
                        "why": ("provider_exec_error_in_post_recon_"
                                f"repair: {str(exc)[:250]}")},
                       recon_ledger, rel, t0,
                       extra={"phase1": p1["metrics"],
                              "phase1_rows": p1["rows"],
                              "relations": rel_metrics,
                              "recon_ledger": recon_ledger})
    result = {"completed": True, "mode": "monolithic_tree_R1_sanitized",
              "objects_in": len(leaf_rows),
              "dispositions": len(tree["fragment_dispositions"]),
              "final_interests":
                  len(assembled["inferred_interests"]),
              "questions": len(assembled["questions"]),
              "regret_candidates": len(assembled["regret_candidates"]),
              "sanitize_status": sanitized_status,
              "recon_wall_s": round(time.monotonic() - t1, 1)}
    return _finish(root, arm, result, recon_ledger, rel, t0,
                   extra={"phase1": p1["metrics"], "phase1_rows": p1["rows"],
                          "relations": rel_metrics,
                          "recon_ledger": recon_ledger})


def _finish(root: Path, arm: str, result: dict, main_ledger: dict,
            rel: tuple | None, t0: float, extra: dict | None = None):
    payload = {
        "generated_at": time.strftime("%Y%m%dT%H%M%S"),
        "arm": arm,
        "result": result,
        "main_ledger": main_ledger,
        "relations_ledger": (rel[0] if isinstance(rel, tuple) else None),
        "extra": extra or {},
        "wall_total_s": round(time.monotonic() - t0, 1),
    }
    ib._write_json(root / "metrics.json", payload)
    print(json.dumps(payload["result"], indent=2)[:900])
    print(f"[v2:{arm}] artifacts -> {root}")
    return 0


def report_mode(root: str) -> int:
    m = json.loads((Path(root) / "metrics.json").read_text(encoding="utf-8"))
    print(json.dumps(m, indent=2)[:4000])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=["D1", "D2", "D3"], required=True)
    ap.add_argument("--artifact-dir", default=None)
    g2 = ap.add_mutually_exclusive_group(required=True)
    g2.add_argument("--run", action="store_true")
    g2.add_argument("--report", metavar="DIR")
    a = ap.parse_args(argv)
    if a.report:
        return report_mode(a.report)
    return run_arm(a.arm, a.artifact_dir)


if __name__ == "__main__":
    sys.exit(main())
