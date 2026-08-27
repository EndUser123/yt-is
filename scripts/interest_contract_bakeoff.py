"""Contract-compliance bakeoff: prose-JSON vs strict schema vs repair.

Executes the FROZEN protocol in
docs/handoffs/interest-intelligence/contract-compliance-bakeoff-
preregistration.md verbatim:

    ARM A  current prose-only JSON mechanism (unmodified control)
    ARM B  provider-native strict output schema + existing validator
    ARM C  Arm B + bounded deterministic/provider reference repair

No semantic policy changes; NO persistence of graph state anywhere.
Unlabeled current-corpus batches only.

Usage:
    python scripts/interest_contract_bakeoff.py --fixtures    # offline self-check
    python scripts/interest_contract_bakeoff.py --run [DIR]   # full bakeoff
    python scripts/interest_contract_bakeoff.py --report DIR  # aggregate metrics
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "build_interest_graph", REPO / "scripts" / "build_interest_graph.py")
big = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(big)

from ef.inference_contract import conformance_errors, \
    inference_output_schema, reconciliation_output_schema

TIMEOUT_S = 580                     # parity with production invocation
ARMS = ("A", "B", "C")
MODEL = "gpt-5.6-luna"              # production requested model (unchanged)
STDERR_TAIL = 2000
CODEX_INSTRUCTION = ("Read {prompt_file} and return ONLY the JSON. "
                     "No prose, no markdown fences.")


# ---------------------------------------------------------------------------
# codex capture: EXACT production invocation (+ schema for arms B/C)
# ---------------------------------------------------------------------------

def _codex_binary() -> str:
    codex = shutil.which("codex")
    if not codex:
        raise FileNotFoundError("codex not found on PATH")
    return codex


def run_codex_capture(prompt_file: Path, schema_file: Path | None,
                      raw_out: Path | None = None) -> dict:
    """One codex exec call in the exact production form (+schema for B/C).

    Extraction uses the production functions from build_interest_graph;
    this adds ONLY measurement (usage events, wall clock, raw stream).
    """
    cmd = [_codex_binary(), "exec", "--json", "--ephemeral", "-s",
           "read-only", "-m", MODEL, "-c", "model_reasoning_effort=medium",
           "-C", "P:/"]
    if schema_file is not None:
        cmd += ["--output-schema", str(schema_file)]
    cmd.append(CODEX_INSTRUCTION.format(prompt_file=prompt_file))

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    t0 = time.monotonic()
    timed_out = False
    stderr = ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=TIMEOUT_S, cwd="P:/",
                           creationflags=creationflags)
        stdout, returncode = r.stdout or "", r.returncode
        stderr = r.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out, returncode = True, None
        out = exc.stdout
        stdout = out.decode(errors="replace") if isinstance(out, bytes) \
            else (out or "")
    latency_s = round(time.monotonic() - t0, 3)
    if raw_out is not None:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_out.write_text(stdout, encoding="utf-8")

    usage = {"input_tokens": 0, "output_tokens": 0,
             "cached_input_tokens": 0}
    error_items = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            u = event.get("usage") or {}
            for key in usage:
                usage[key] += int(u.get(key) or 0)
        elif event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "error":
                error_items.append(str(item.get("message", ""))[:200])
    return {
        "text": big.extract_agent_message(stdout),
        "usage": usage,
        "latency_s": latency_s,
        "returncode": returncode,
        "timed_out": timed_out,
        "error_items": error_items,
        "stderr_tail": stderr[-STDERR_TAIL:],
    }


def _extract_payload(cap: dict):
    """Production-equivalent parse; raises ProviderExecutionError."""
    if cap["text"] is None:
        raise big.ProviderExecutionError(
            "no agent_message in provider event stream "
            f"(rc={cap['returncode']}, errors={cap['error_items'][:2]})")
    return big.extract_json_object(cap["text"])


def _safe_extract(cap: dict):
    """Same as _extract_payload but returns the error instead of raising."""
    if cap["text"] is None:
        return big.ProviderExecutionError(
            "no agent_message in provider event stream "
            f"(rc={cap['returncode']}, errors={cap['error_items'][:2]})")
    try:
        return big.extract_json_object(cap["text"])
    except big.ProviderExecutionError as exc:
        return exc


# ---------------------------------------------------------------------------
# batch execution + frozen metric classification
# ---------------------------------------------------------------------------

def _accumulate(record: dict, cap: dict) -> None:
    record["latency_s"] = round(record["latency_s"] + cap["latency_s"], 3)
    for key in record["usage"]:
        record["usage"][key] += cap["usage"].get(key, 0)


def _fail(record: dict, status: str, detail: str) -> dict:
    record.update({"status": status, "failure_class": status,
                   "detail": detail[:500], "semantic_valid_final": False})
    return record


def execute_batch(arm: str, plan_id: str, batch_id: str, cluster_rows,
                  call_dir: Path, schema_file: Path | None) -> dict:
    """One planned batch for one arm; classification per frozen metrics."""
    supplied = sorted(int(c["cluster_id"]) for c in cluster_rows)
    prompts_dir = call_dir.parent / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompts_dir / f"{batch_id}.txt"
    prompt_text = big.build_prompt(cluster_rows)
    prompt_file.write_text(prompt_text, encoding="utf-8")
    batch_schema = schema_file if arm in ("B", "C") else None

    record = {"arm": arm, "batch_id": batch_id, "cluster_ids": supplied,
              "plan_id": plan_id, "latency_s": 0.0, "retries_used": 0,
              "schema_attached": bool(batch_schema), "attempted": True,
              "usage": {"input_tokens": 0, "output_tokens": 0,
                        "cached_input_tokens": 0}}

    parsed = None
    last_status = last_detail = ""
    for attempt in (1, 2):
        cap = run_codex_capture(
            prompt_file, batch_schema,
            raw_out=call_dir / f"{batch_id}.{arm}.raw{attempt}.jsonl")
        _accumulate(record, cap)
        if cap["returncode"] != 0 or cap["timed_out"]:
            last_status = ("provider_timeout" if cap["timed_out"]
                           else "provider_exec_error")
            last_detail = cap["stderr_tail"] or "; ".join(cap["error_items"])
        elif cap["text"] is None:
            last_status, last_detail = "no_agent_message", \
                "; ".join(cap["error_items"]) or "empty event stream"
        else:
            extracted = _safe_extract(cap)
            if isinstance(extracted, big.ProviderExecutionError):
                # parse failure WITH an agent message present is a data
                # point, not transient infra — never retried (prereg §4)
                return _fail(record, "no_json_extractable", str(extracted))
            parsed = extracted
            last_status = ""
            break
        if attempt == 1:                     # single exec-class retry only
            record["retries_used"] = 1
            record["retry_of"] = last_status
    if last_status:
        return _fail(record, last_status, last_detail)

    record["structurally_valid"] = True
    payload = parsed
    record["original_hash"] = big.canonical_result_hash(payload)

    schema_errs = ([] if arm == "A" else conformance_errors(
        payload, inference_output_schema()))
    if schema_errs:
        # FROZEN RULE 3: enforcement failure -> counted, NEVER repaired,
        # fail closed. It must not reach validation-or-fragments.
        return _fail(record, "schema_violation",
                     f"schema_enforcement_failure: "
                     f"{json.dumps(schema_errs[:6])}")

    receipts: list = []
    repair_applied = False
    if arm == "C":
        payload, hyg = big.deterministic_reference_hygiene(payload)
        receipts.extend(hyg)
        repair_applied = bool(hyg)

    try:
        big.validate_inference(payload, set(supplied))
        record["semantic_valid_first_pass"] = True
    except big.InferenceContractError as exc:
        record["semantic_valid_first_pass"] = False
        cls = big.classify_contract_error(exc)
        if arm != "C" or cls != "reference":
            return _fail(record, "semantic_invalid",
                         f"semantic_{cls}: {exc}")
        # bounded reference repair under the same strict schema

        repair_call_seq = [0]

        def invoke(prompt: str) -> dict:
            repair_call_seq[0] += 1
            rp = call_dir / (f"{batch_id}.{arm}.repair-prompt"
                             f"-{repair_call_seq[0]}.txt")
            rp.write_text(prompt, encoding="utf-8")
            rcap = run_codex_capture(
                rp, batch_schema,
                raw_out=call_dir /
                f"{batch_id}.{arm}.repair.raw{repair_call_seq[0]}.jsonl")
            _accumulate(record, rcap)
            return _extract_payload(rcap)

        before = big.canonical_result_hash(payload)
        try:
            payload, rep_receipts, used = big.validated_reference_repair(
                payload, set(supplied), invoke)
        except big.InferenceContractError as exc2:
            fail_cls = "repair_exhausted" if "converge" in str(exc2) \
                else "repair_out_of_scope_fail_closed"
            return _fail(record, "repair_failed_closed",
                         f"{fail_cls}: {exc2}")
        except big.ProviderExecutionError as exc3:
            # a flake during repair must not abort the bakeoff (prereg §3);
            # bounded-by-design, so classify fail-closed, no extra retries
            return _fail(record, "repair_failed_closed",
                         f"provider_exec_error_in_repair: {exc3}")
        receipts.extend(rep_receipts)
        receipts.append({"repair_type": "hash_chain", "before": before,
                         "after": big.canonical_result_hash(payload)})
        record["repair_attempts"] = used
        repair_applied = True

    record.update({"status": "semantic_valid_final",
                   "semantic_valid_final": True,
                   "repaired": repair_applied,
                   "dropped_edges": sum(
                       1 for r in receipts
                       if str(r.get("repair_type", "")).startswith("drop_"))})
    record["final_hash"] = big.canonical_result_hash(payload)
    fragments = big.build_fragments(plan_id, batch_id, payload)
    (call_dir / f"{batch_id}.{arm}.validated.json").write_text(
        json.dumps({"receipts": receipts, "payload": payload},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    record["_fragments"] = fragments
    return record


# ---------------------------------------------------------------------------
# reconciliation per arm (own fragments; own invoke style)
# ---------------------------------------------------------------------------

def make_recon_adapter(arm: str, schemas: dict, ledger: list):
    """Adapter matching _reconcile_group's expected invoke signature while
    recording usage/latency exactly like batch calls do. Writes its own
    uniquely numbered prompt/raw-stream pair per call so the audit trail
    is never overwritten between groups or attempts."""
    recon_schema = schemas["reconciliation"] if arm in ("B", "C") else None
    seq = [0]

    def adapter(provider: str, prompt: str, prompt_file: Path,
                timeout: int):
        del timeout                                  # TIMEOUT_S fixed
        seq[0] += 1
        own = prompt_file.parent / (f"{prompt_file.stem}"
                                    f"-call{seq[0]:03d}.txt")
        own.write_text(prompt, encoding="utf-8")
        cap = run_codex_capture(
            own, recon_schema,
            raw_out=own.parent / f"{own.stem}.raw.jsonl")
        entry = {"tag": own.stem, "latency_s": cap["latency_s"],
                 "usage": cap["usage"],
                 "schema_attached": bool(recon_schema)}
        try:
            parsed = _extract_payload(cap)
        except big.ProviderExecutionError:
            entry["status"] = "no_json_extractable"
            ledger.append(entry)
            raise
        entry["status"] = "parsed"
        ledger.append(entry)
        return parsed, MODEL

    return adapter


def make_c_hook(schemas: dict, ledger: list, eligible_ids, prompts_dir: Path):
    """Arm C final-only repair hook (scope ceiling enforced here).

    The hygiene input is a DEEP COPY of the whole final payload, so valid
    questions/regret sections survive untouched; provider repair prompts
    and raw streams land in prompts_dir with unique names.
    """
    import copy as _copy
    seq = [0]

    def hook(wrapper, group_fragments, stage: int, group_index: int):
        del group_fragments
        allowed = set(eligible_ids)
        final_copy = _copy.deepcopy(wrapper["final"])
        cleaned, hyg = big.deterministic_reference_hygiene(final_copy)
        receipt_entry = {"stage": stage, "group_index": group_index,
                         "hygiene": hyg, "attempts_used": 0}
        try:
            big.validate_inference(cleaned, allowed)
        except big.InferenceContractError as exc:
            if big.classify_contract_error(exc) != "reference":
                raise
            # disposition audit surface untouched; repairs target `final`
            def invoke(prompt: str) -> dict:
                seq[0] += 1
                p = prompts_dir / (f"c-hook-s{stage}-g{group_index}"
                                   f"-repair{seq[0]}.txt")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(prompt, encoding="utf-8")
                cap = run_codex_capture(
                    p, schemas["inference"],
                    raw_out=p.parent / f"{p.stem}.raw.jsonl")
                receipt_entry["provider_calls"] = \
                    receipt_entry.get("provider_calls", 0) + 1
                receipt_entry.setdefault("usage", {"input_tokens": 0,
                                                   "output_tokens": 0})
                for k in receipt_entry["usage"]:
                    receipt_entry["usage"][k] += cap["usage"].get(k, 0)
                return _extract_payload(cap)

            repaired, _, used = big.validated_reference_repair(
                cleaned, allowed, invoke)
            receipt_entry["attempts_used"] = used
            cleaned = repaired
        receipt_entry["final_interests"] = len(
            cleaned["inferred_interests"])
        ledger.append(receipt_entry)
        fixed = dict(wrapper)
        fixed["final"] = cleaned
        return fixed
    return hook


# ---------------------------------------------------------------------------
# modes
# ---------------------------------------------------------------------------

def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def _agg(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    out = {"n": len(values), "mean": round(statistics.fmean(values), 3),
           "p50": round(statistics.median(values), 3)}
    out["p95"] = values[max(0, int(round(0.95 * len(values))) - 1)]
    return out


def summarize(records: list[dict]) -> dict:
    arms = {}
    for arm in ARMS:
        rs = [r for r in records if r["arm"] == arm]
        attempted = [r for r in rs if r.get("attempted")]
        valid_final = [r for r in rs if r.get("semantic_valid_final")]
        classes: dict[str, int] = {}
        for r in rs:
            if not r.get("semantic_valid_final"):
                classes[r["failure_class"]] = \
                    classes.get(r["failure_class"], 0) + 1
        arms[arm] = {
            "attempted": len(attempted),
            "structurally_valid": sum(bool(r.get("structurally_valid"))
                                      for r in rs),
            "first_pass_valid": sum(bool(r.get("semantic_valid_first_pass"))
                                    for r in rs),
            "repaired": sum(bool(r.get("repaired")) for r in rs),
            "semantic_valid_final": len(valid_final),
            "unrecoverable": len(attempted) - len(valid_final),
            "retry_events": sum(r.get("retries_used", 0) for r in rs),
            "failure_classes": classes,
            "dropped_edge_repairs": sum(r.get("dropped_edges", 0)
                                        for r in rs),
            "tokens": {k: sum(r["usage"].get(k, 0) for r in rs)
                       for k in ("input_tokens", "output_tokens",
                                 "cached_input_tokens")},
            "latency_s": _agg([r["latency_s"] for r in rs]),
        }
    return arms


def run_mode(artifact_dir: str | None) -> int:
    from csf.paths import load_workspace_env
    load_workspace_env()
    from ef.evidence_clusters import evidence_cluster_inventory, \
        hydrate_evidence_clusters
    from ef.interest_candidates import build_bootstrap_plan, \
        validate_plan_coverage

    inventory = evidence_cluster_inventory()
    plan = build_bootstrap_plan(
        inventory["clusters"],
        max_per_call=big.BOOTSTRAP_MAX_CLUSTERS_PER_CALL,
        exclusions=inventory.get("exclusions", {}))
    validate_plan_coverage(plan, big.BOOTSTRAP_MAX_CLUSTERS_PER_CALL)

    stamp = time.strftime("%Y%m%dT%H%M%S")
    root = Path(artifact_dir) if artifact_dir else (
        big.ARTIFACT_ROOT / f"bakeoff-{stamp}_{plan.plan_id}")
    calls = root / "calls"
    recon_dir = root / "reconciliation"
    root.mkdir(parents=True, exist_ok=True)

    # schemas + freeze proof
    schema_paths = {
        "inference": root / "schemas" / "inference-output-schema.json",
        "reconciliation": root / "schemas" /
                          "reconciliation-output-schema.json"}
    _write_json(schema_paths["inference"], inference_output_schema())
    _write_json(schema_paths["reconciliation"],
                reconciliation_output_schema())
    prereg = REPO / ("docs/handoffs/interest-intelligence/"
                     "contract-compliance-bakeoff-preregistration.md")
    _write_json(root / "preregistration.json", {
        "path": str(prereg),
        "sha256": hashlib.sha256(prereg.read_bytes()).hexdigest()})
    _write_json(root / "plan.json", plan.to_dict())

    hydrated = {b.batch_id: hydrate_evidence_clusters(list(b.cluster_ids))
                for b in plan.batches}

    records: list[dict] = []
    arm_fragments = {arm: {"interests": [], "questions": [],
                           "regret_candidates": []} for arm in ARMS}
    for b in plan.batches:
        rows = hydrated[b.batch_id]
        for arm in ARMS:                     # interleaved per protocol §2
            try:
                rec = execute_batch(arm, plan.plan_id, b.batch_id, rows,
                                    calls, schema_paths["inference"])
            except Exception as exc:
                # prereg §3: NO abort — a harness bug becomes one recorded
                # failure-class row and the diagnostic keeps running
                rec = {"arm": arm, "batch_id": b.batch_id,
                       "cluster_ids": [], "attempted": True,
                       "status": "harness_error",
                       "failure_class": "harness_error",
                       "detail": f"{type(exc).__name__}: {exc}"[:500],
                       "semantic_valid_final": False, "latency_s": 0.0,
                       "usage": {"input_tokens": 0, "output_tokens": 0,
                                 "cached_input_tokens": 0}}
            frags = rec.pop("_fragments", None)
            records.append(rec)
            _write_json(calls / f"{b.batch_id}.{arm}.record.json", rec)
            if frags:
                arm_fragments[arm]["interests"].extend(frags["interests"])
                arm_fragments[arm]["questions"].extend(frags["questions"])
                arm_fragments[arm]["regret_candidates"].extend(
                    frags["regret_candidates"])
            print(f"[bakeoff] {b.batch_id} {arm}: {rec['status']}"
                  f" ({rec['latency_s']}s)")

    recon_results = {}
    for arm in ARMS:
        frag = arm_fragments[arm]
        n_frag = len(frag["interests"])
        if n_frag == 0:
            recon_results[arm] = {"status": "skipped_no_fragments",
                                  "fragments": 0}
            continue
        ledger: list = []
        hook = (make_c_hook(schema_paths, ledger,
                            list(plan.eligible_cluster_ids),
                            recon_dir / "c-hook-prompts")
                if arm == "C" else None)
        t0 = time.monotonic()
        try:
            tree = big.run_reconciliation_tree(
                frag, list(plan.eligible_cluster_ids), provider="codex",
                timeout=TIMEOUT_S,
                prompt_path=recon_dir / f"{arm}-group-prompt.txt",
                invoke=make_recon_adapter(arm, schema_paths, ledger),
                stage_writer=lambda s, rec_, a=arm: _write_json(
                    recon_dir / f"{a}-stage-{s:02d}.json", rec_),
                repair_hook=hook)
            big.validate_inference(tree["final"],
                                   set(plan.eligible_cluster_ids))
            recon_results[arm] = {
                "status": "completed", "fragments": n_frag,
                "stages": len(tree["stages"]),
                "leaf_dispositions": len(tree["fragment_dispositions"]),
                "final_interests":
                    len(tree["final"]["inferred_interests"]),
                "group_call_records": ledger,
                "wall_s": round(time.monotonic() - t0, 1)}
            _write_json(recon_dir / f"{arm}-final.json",
                        {"final": tree["final"],
                         "dispositions": tree["fragment_dispositions"]})
            print(f"[bakeoff] recon {arm}: COMPLETED ({n_frag} fragments)")
        except Exception as exc:
            recon_results[arm] = {
                "status": "failed", "fragments": n_frag,
                "error_type": type(exc).__name__,
                "error": str(exc)[:800], "group_call_records": ledger,
                "wall_s": round(time.monotonic() - t0, 1)}
            print(f"[bakeoff] recon {arm}: FAILED ({type(exc).__name__})")

    recon_results_serializable = {}
    for arm, res in recon_results.items():
        res2 = dict(res)
        res2.pop("group_call_records", None)     # ledgered separately
        res2["ledger"] = res.get("group_call_records", [])
        recon_results_serializable[arm] = res2

    metrics = {
        "generated_at": stamp,
        "plan_id": plan.plan_id,
        "eligible_clusters": len(plan.eligible_cluster_ids),
        "batches": len(plan.batches),
        "arms": summarize(records),
        "records": records,
        "reconciliation": recon_results_serializable,
    }
    _write_json(root / "metrics.json", metrics)
    print(json.dumps({a: m for a, m in metrics["arms"].items()}, indent=2)[:1500])
    print(f"[bakeoff] artifacts -> {root}")
    return 0


def report_mode(root: str) -> int:
    metrics = json.loads((Path(root) / "metrics.json").read_text(
        encoding="utf-8"))
    for arm in ARMS:
        m = metrics["arms"][arm]
        print(f"\nARM {arm}: final-valid {m['semantic_valid_final']}/"
              f"{m['attempted']}  unrecoverable={m['unrecoverable']}  "
              f"repaired={m['repaired']}  first-pass="
              f"{m['first_pass_valid']}")
        print(f"  failure_classes: {json.dumps(m['failure_classes'])}")
        r = metrics["reconciliation"][arm]
        print(f"  reconciliation: {r['status']} "
              f"(stages={r.get('stages')}, fragments={r.get('fragments')})")
    print(f"\nplan: {metrics['plan_id']} "
          f"({metrics['eligible_clusters']} clusters / "
          f"{metrics['batches']} batches)")
    return 0


# ---------------------------------------------------------------------------
# offline synthetic fixtures (harness correctness, NOT compliance data)
# ---------------------------------------------------------------------------

def fixtures_mode() -> int:
    failures = []

    def check(name: str, cond: bool):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    def payload():
        return {
            "inferred_interests": [
                {"name": "Distributed Databases", "kind": "domain",
                 "parent": None, "temporal_state": "durable",
                 "stance": "learning", "confidence": 0.9,
                 "observed_vs_inferred": "observed", "goal": None,
                 "information_need": None, "cluster_ids": [1],
                 "evidence_summary": "s", "counterevidence": None,
                 "related_to": ["Raft Consensus"]},
                {"name": "Raft Consensus", "kind": "subtopic",
                 "parent": "Distributed Databases",
                 "temporal_state": "active", "stance": "project",
                 "confidence": 0.8, "observed_vs_inferred": "inferred",
                 "goal": None, "information_need": None,
                 "cluster_ids": [2], "evidence_summary": "s2",
                 "counterevidence": None, "related_to": []}],
            "questions": [{"text": "q?", "interest": "Raft Consensus",
                           "status": "open"}],
            "regret_candidates": [],
        }

    schema = inference_output_schema()
    supplied = {1, 2}
    good = payload()
    big.validate_inference(good, supplied)
    check("fixture-good-passes-both-layers",
          conformance_errors(good, schema) == [])

    cases = []
    bad_enum = payload()
    bad_enum["inferred_interests"][0]["temporal_state"] = "ongoing"
    cases.append(("invalid-temporal-state", bad_enum, "schema"))
    bad_conf = payload()
    bad_conf["inferred_interests"][0]["confidence"] = 1.5
    cases.append(("confidence-out-of-bounds", bad_conf, "schema"))
    bad_bool = payload()
    bad_bool["inferred_interests"][0]["confidence"] = True
    cases.append(("boolean-confidence-type", bad_bool, "schema"))
    extra = payload()
    extra["inferred_interests"][0]["summary_extra"] = "x"
    cases.append(("unknown-property", extra, "schema"))
    missing_field = payload()
    del missing_field["inferred_interests"][0]["stance"]
    cases.append(("missing-required-field", missing_field, "schema"))

    dangling = payload()
    dangling["inferred_interests"][0]["related_to"] = ["Ghost Interest"]
    cases.append(("dangling-related-to-schema-clean", dangling, "clean"))

    orphan_q = payload()
    orphan_q["questions"][0]["interest"] = "Nope"
    cases.append(("orphan-question-schema-clean", orphan_q, "clean"))

    for name, pl, expectation in cases:
        errs = conformance_errors(pl, schema)
        if expectation == "schema":
            check(name, bool(errs))
        else:
            check(name, errs == [])

    # classifier scope
    try:
        big.validate_inference(dangling, supplied)
        check("dangling-classifier", False)
    except big.InferenceContractError as exc:
        check("dangling-classifier",
              big.classify_contract_error(exc) == "reference")
    try:
        big.validate_inference(bad_enum, supplied)
        check("enum-classifier", False)
    except big.InferenceContractError as exc:
        check("enum-classifier",
              big.classify_contract_error(exc) == "other")

    # deterministic hygiene receipts
    cleaned, receipts = big.deterministic_reference_hygiene(dangling)
    check("hygiene-drops-dangling-edge",
          cleaned["inferred_interests"][0]["related_to"] == [] and
          len(receipts) == 1 and
          receipts[0]["repair_type"] == "drop_dangling_related_to")
    big.validate_inference(cleaned, supplied)   # lossless -> now valid

    # bounded repair via fake provider invoke
    def fixed_invoke(prompt: str) -> dict:
        fixed = payload()
        fixed["questions"][0]["interest"] = "Raft Consensus"
        return fixed
    orphan_hyg = big.deterministic_reference_hygiene(orphan_q)[0]
    out, rep_receipts, used = big.validated_reference_repair(
        orphan_hyg, supplied, fixed_invoke)
    check("bounded-repair-converges", used == 1 and
          any(r.get("repair_type") == "reference_repair_applied"
              for r in rep_receipts))
    big.validate_inference(out, supplied)

    # extraction parity: fenced + bare agent_message forms
    sample_obj = {"inferred_interests": [], "questions": [],
                  "regret_candidates": []}
    fenced = '```json\n' + json.dumps(sample_obj) + '\n```'
    for form in (fenced, json.dumps(sample_obj)):
        wrapped = json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": form}})
        text = big.extract_agent_message(wrapped)
        parsed = big.extract_json_object(text)
        check("extraction-parity", parsed == sample_obj)

    total = 15 + len(cases)
    print(f"\nfixtures: {total - len(failures)}/{total} passed")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fixtures", action="store_true")
    g.add_argument("--run", nargs="?", const="", metavar="ARTIFACT_DIR")
    g.add_argument("--report", metavar="ARTIFACT_DIR")
    a = ap.parse_args(argv)
    if a.fixtures:
        return fixtures_mode()
    if a.run is not None:
        return run_mode(a.run or None)
    return report_mode(a.report)


if __name__ == "__main__":
    sys.exit(main())
