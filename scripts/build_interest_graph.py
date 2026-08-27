"""v2: LLM inference layer — evidence packets → typed interest/goal graph.

The operator's contract (personal-intelligence-system design):
  Given evidence clusters containing semantic topics, entities,
  representative documents, temporal statistics, source diversity:
  infer interests, goals, information needs, questions. Not topics.

Boundary (2026-08-24 contract-fidelity packet):
  evidence clusters → provider → STRICT MECHANICAL VALIDATION →
  transactional typed-graph persistence.

  - json.loads is NOT validation: validate_inference() enforces required
    structure, enums, confidence bounds, evidence-cluster references
    against the exact supplied packet, and internal interest/question
    relationships (parents, cycles, related_to) before anything is
    persisted.
  - Invalid provider output fails closed: no semantic DB mutation, and
    no canonical result artifact is written.

Bootstrap (2026-08-24 full-coverage packet):
  complete eligible cluster universe → deterministic bounded batches
  (<= 25 clusters/call) → validated batch FRAGMENTS (never persisted
  directly) → bounded auditable reconciliation tree → one final V2
  payload → optional transactional persistence. The old single-shot
  top-25 breadth path remains only as an explicit evaluation BASELINE.

Usage:
    python scripts/build_interest_graph.py --dry-run     # show packets
    python scripts/build_interest_graph.py --plan-bootstrap   # 0 provider calls
    python scripts/build_interest_graph.py --plan-baseline    # 0 provider calls
    python scripts/build_interest_graph.py --run-bootstrap --allow-spend
    python scripts/build_interest_graph.py --run-bootstrap --allow-spend --store
    python scripts/build_interest_graph.py --provider agy ...  # provider override
    python scripts/build_interest_graph.py                   # LEGACY single-shot
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef.inference_contract import conformance_errors  # noqa: E402
from ef.inference_contract import KINDS, QUESTION_STATUSES, REGRET_LABELS  # noqa: E402
from ef.inference_contract import OBSERVED_VS_INFERRED, STANCES  # noqa: E402
from ef.inference_contract import TEMPORAL_STATES  # noqa: E402
from ef.inference_contract import (inference_output_schema,  # noqa: E402
                                   reconciliation_output_schema)

MAX_CLUSTERS = 25          # LEGACY single-shot baseline only: one global
                           # top-25 breadth-ranked subset. Bootstrap replaces
                           # this truncation; kept for the evaluation baseline.
MAX_REPS_PER_CLUSTER = 4   # representative docs per packet
PROMPT_VERSION = "v2.1-contract-fidelity"
CANDIDATE_POLICY = f"top{MAX_CLUSTERS}-breadth-biased"   # legacy/baseline
STDERR_DIAGNOSTIC_LIMIT = 2000

# Full-coverage bootstrap bounds. Dashboard-style top-N is allowed;
# inference bootstrap top-N is NOT.
BOOTSTRAP_MAX_CLUSTERS_PER_CALL = 25
MAX_FRAGMENTS_PER_RECONCILIATION = 40
MAX_RECONCILIATION_STAGES = 8     # fail closed beyond this — never truncate
ARTIFACT_ROOT = Path("P:/.data/yt-is/ef/interest-inference")


def _new_run_dir(kind: str) -> Path:
    """Unique per-execution artifact root under the canonical store.

    Artifact-hygiene rule (2026-08-26 additive packet): no inference run
    may write to a FIXED shared path — one run gets one unique directory,
    so concurrent runs cannot overwrite each other and a stale prior-run
    payload can never masquerade as the current result. Nothing ever
    reads these directories implicitly.
    """
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}_{kind}"
    return ARTIFACT_ROOT / "runs" / run_id


class ProviderExecutionError(RuntimeError):
    """Provider subprocess failed, emitted no usable agent message, or its
    output contained no extractable JSON. Distinct from a contract
    violation: nothing was parsed into a candidate semantic object."""


class InferenceContractError(ValueError):
    """Provider output violated the v2 semantic contract. The payload must
    not be persisted; the caller fails closed before any DB mutation."""


PROMPT_TEMPLATE = """You are analyzing a personal corpus of {n_clusters} evidence clusters from a multi-source intelligence system (YouTube, Discord, Reddit, RSS, HN, GitHub, podcasts). Your task is NOT to classify topics — it is to infer what this person is actually trying to understand, accomplish, decide, monitor, improve, build, prevent, or understand.

For each evidence cluster below, determine whether it supports a genuine user information interest. Do not merely rename the cluster. Infer, when supported:
- domain (broad area)
- topic (specific subject within the domain)
- underlying goal/problem (what outcome the person appears to care about)
- information need (what they are repeatedly trying to learn)

Distinguish:
- observed subject matter (what the documents are about)
- inferred durable interest (long-term pattern)
- current project/decision (active, recent, decision-shaped)
- curiosity/entertainment (broad consumption, no project signal)
- inferred adjacent interest (implied by goals but not directly observed)

Merge clusters when they are better explained by one latent goal (e.g., sleep + exercise + ApoB + Alzheimer's → "preserve cognition and extend healthy lifespan"). Keep them separate when shared vocabulary is superficial.

For every inference, cite the cluster_id. Record counterevidence where present.

Return ONLY valid JSON matching this schema:
{{
  "inferred_interests": [
    {{
      "name": "string — canonical name",
      "kind": "domain|topic|subtopic|method|monitor",
      "parent": "name of parent interest or null",
      "temporal_state": "durable|active|current_problem|episodic|emerging|dormant",
      "stance": "curiosity|learning|project|monitoring|entertainment",
      "confidence": 0.0-1.0,
      "observed_vs_inferred": "observed|inferred|inferred_adjacent",
      "goal": "string — the underlying goal/problem, or null",
      "information_need": "string — what they are repeatedly trying to learn, or null",
      "cluster_ids": [int],
      "evidence_summary": "string — what evidence supports this",
      "counterevidence": "string — what argues against this, or null",
      "related_to": ["name of related interests"]
    }}
  ],
  "questions": [
    {{
      "text": "string — an open question the person appears to be investigating",
      "interest": "name of the parent interest",
      "status": "open|watching"
    }}
  ],
  "regret_candidates": [
    {{
      "topic": "string — adjacent topic poorly represented but strongly implied",
      "why": "string — why this matters given demonstrated goals",
      "label": "inferred_adjacent",
      "confidence": 0.0-1.0,
      "cluster_ids": [int],
      "related_interests": ["name of related interests"]
    }}
  ]
}}

Hard constraints:
- cluster_ids must reference ONLY cluster ids supplied above.
- parent, related_to, questions.interest, and regret.related_interests must
  reference interest names that appear in this same JSON's inferred_interests.
- Every interest needs a non-empty evidence_summary and at least one
  cluster_id. confidence is a number between 0 and 1 (not true/false).

EVIDENCE CLUSTERS:

{packets}
"""


def build_packets(clusters: list[dict]) -> str:
    """Format evidence clusters into the LLM packet text."""
    parts = []
    for c in clusters[:MAX_CLUSTERS]:
        reps = "\n".join(
            f"    - \"{r['title']}\" ({r['month']}, {r['source']})"
            for r in c["representative"][:MAX_REPS_PER_CLUSTER])
        ents = ", ".join(e["entity"] for e in c["entities"][:8])
        parts.append(f"""Cluster {c['cluster_id']}: {c['label']}
  Terms: {', '.join(c['terms'][:8])}
  Entities: {ents or '(none passing specificity)'}
  Stats: {c['channels']} channels, {c['documents']} docs,
         {c['active_months']} active months ({c['first_month']} to {c['last_month']}),
         sources: {dict(c['sources'])}
  Phase: {c.get('phase') or 'steady'}
  Representative documents:
{reps}""")
    return "\n\n".join(parts)


def build_prompt(clusters: list[dict]) -> str:
    return PROMPT_TEMPLATE.format(
        n_clusters=min(len(clusters), MAX_CLUSTERS),
        packets=build_packets(clusters))


# ---------------------------------------------------------------------------
# Provider output parsing
# ---------------------------------------------------------------------------

def extract_agent_message(stdout: str) -> str | None:
    """Return the last agent_message text from codex JSONL events, or None."""
    agent_text = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                agent_text = item.get("text") or agent_text
    return agent_text


def extract_json_object(text: str) -> dict:
    """Extract the first plausible JSON object from provider wrapper text.

    Raises ProviderExecutionError when no JSON object can be extracted —
    this is an execution/parse failure, not a contract violation.
    """
    candidates = []
    start = text.find("```json")
    if start >= 0:
        end = text.find("```", start + 7)
        if end > start:
            candidates.append(text[start + 7:end])
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start >= 0:
            end = text.rfind(closer)
            if end > start:
                candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ProviderExecutionError(
        "no JSON object found in provider output: " f"{text[:200]!r}")


# ---------------------------------------------------------------------------
# Mechanical contract validation
# ---------------------------------------------------------------------------

def _norm_name(value) -> str:
    return " ".join(str(value).strip().casefold().split())


def _is_confidence(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0.0 <= value <= 1.0)


def _require_nonempty_str(obj: dict, field: str, where: str) -> str:
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InferenceContractError(f"{where}: {field} must be a non-empty "
                                     f"string, got {value!r}")
    return value


def validate_inference(payload, supplied_cluster_ids) -> None:
    """Fail closed on any violation of the v2 inference contract.

    supplied_cluster_ids is the exact set of cluster ids given to the
    provider in this invocation; every referenced cluster id must be a
    member. Raises InferenceContractError with a specific message.
    """
    supplied = set(supplied_cluster_ids)
    if not isinstance(payload, dict):
        raise InferenceContractError(
            f"top level must be a JSON object, got {type(payload).__name__}")
    for key in ("inferred_interests", "questions", "regret_candidates"):
        if key not in payload:
            raise InferenceContractError(f"missing required top-level "
                                         f"array: {key}")
        if not isinstance(payload[key], list):
            raise InferenceContractError(
                f"top-level {key} must be an array, got "
                f"{type(payload[key]).__name__}")
    interests = payload["inferred_interests"]
    if not interests:
        raise InferenceContractError(
            "inferred_interests is empty — silently dropping all inferred "
            "state is a contract violation")

    names = set()
    for i, it in enumerate(interests):
        where = f"interest[{i}]"
        if not isinstance(it, dict):
            raise InferenceContractError(f"{where} must be an object")
        name = _require_nonempty_str(it, "name", where)
        if _norm_name(name) in names:
            raise InferenceContractError(
                f"{where}: duplicate interest name (case-insensitive): "
                f"{name!r}")
        names.add(_norm_name(name))

    for i, it in enumerate(interests):
        where = f"interest[{i}] ({it.get('name')!r})"
        if it.get("kind") not in KINDS:
            raise InferenceContractError(f"{where}: invalid kind "
                                         f"{it.get('kind')!r}")
        if it.get("temporal_state") not in TEMPORAL_STATES:
            raise InferenceContractError(f"{where}: invalid temporal_state "
                                         f"{it.get('temporal_state')!r}")
        if it.get("stance") not in STANCES:
            raise InferenceContractError(f"{where}: invalid stance "
                                         f"{it.get('stance')!r}")
        if it.get("observed_vs_inferred") not in OBSERVED_VS_INFERRED:
            raise InferenceContractError(
                f"{where}: invalid observed_vs_inferred "
                f"{it.get('observed_vs_inferred')!r}")
        if not _is_confidence(it.get("confidence")):
            raise InferenceContractError(
                f"{where}: confidence must be a number in [0,1], got "
                f"{it.get('confidence')!r}")
        for field in ("goal", "information_need", "counterevidence"):
            value = it.get(field)
            if value is not None and (not isinstance(value, str)
                                      or not value.strip()):
                raise InferenceContractError(
                    f"{where}: {field} must be a non-empty string or null, "
                    f"got {value!r}")
        cluster_ids = it.get("cluster_ids")
        if not isinstance(cluster_ids, list) or not cluster_ids:
            raise InferenceContractError(
                f"{where}: cluster_ids must be a non-empty list")
        _validate_cluster_ids(cluster_ids, supplied, where, dupes=True)
        _require_nonempty_str(it, "evidence_summary", where)
        related = it.get("related_to")
        if not isinstance(related, list):
            raise InferenceContractError(f"{where}: related_to must be a list")
        for target in related:
            if _norm_name(target) not in names:
                raise InferenceContractError(
                    f"{where}: related_to target not in returned interests: "
                    f"{target!r}")
        parent = it.get("parent")
        if parent is not None:
            if not isinstance(parent, str) or not parent.strip():
                raise InferenceContractError(
                    f"{where}: parent must be a non-empty string or null")
            if _norm_name(parent) == _norm_name(it["name"]):
                raise InferenceContractError(f"{where}: parent references "
                                             "itself")
            if _norm_name(parent) not in names:
                raise InferenceContractError(
                    f"{where}: parent not in returned interests: {parent!r}")

    _validate_parent_cycles(interests, names)

    for i, q in enumerate(payload["questions"]):
        where = f"question[{i}]"
        if not isinstance(q, dict):
            raise InferenceContractError(f"{where} must be an object")
        _require_nonempty_str(q, "text", where)
        if q.get("status") not in QUESTION_STATUSES:
            raise InferenceContractError(f"{where}: invalid status "
                                         f"{q.get('status')!r}")
        if _norm_name(q.get("interest")) not in names:
            raise InferenceContractError(
                f"{where}: interest not in returned interests: "
                f"{q.get('interest')!r}")

    for i, rc in enumerate(payload["regret_candidates"]):
        where = f"regret_candidate[{i}]"
        if not isinstance(rc, dict):
            raise InferenceContractError(f"{where} must be an object")
        _require_nonempty_str(rc, "topic", where)
        _require_nonempty_str(rc, "why", where)
        if rc.get("label") not in REGRET_LABELS:
            raise InferenceContractError(f"{where}: label must be "
                                         f"'inferred_adjacent', got "
                                         f"{rc.get('label')!r}")
        if not _is_confidence(rc.get("confidence")):
            raise InferenceContractError(
                f"{where}: confidence must be a number in [0,1], got "
                f"{rc.get('confidence')!r}")
        _validate_cluster_ids(rc.get("cluster_ids"), supplied, where,
                              dupes=False)
        for target in rc.get("related_interests", []):
            if _norm_name(target) not in names:
                raise InferenceContractError(
                    f"{where}: related_interests target not in returned "
                    f"interests: {target!r}")


def _validate_cluster_ids(cluster_ids, supplied: set, where: str,
                          *, dupes: bool) -> None:
    if not isinstance(cluster_ids, list):
        raise InferenceContractError(f"{where}: cluster_ids must be a list")
    seen = set()
    for cid in cluster_ids:
        if type(cid) is not int:
            raise InferenceContractError(
                f"{where}: cluster id must be an integer, got {cid!r}")
        if cid not in supplied:
            raise InferenceContractError(
                f"{where}: cluster id {cid} was not in the supplied "
                f"evidence packet")
        if dupes and cid in seen:
            raise InferenceContractError(
                f"{where}: duplicate cluster id {cid}")
        seen.add(cid)


def _validate_parent_cycles(interests, names) -> None:
    parents = {}
    for it in interests:
        if it.get("parent"):
            parents[_norm_name(it["name"])] = _norm_name(it["parent"])
    for start in names:
        walked = set()
        node = start
        while node in parents:
            if node in walked:
                raise InferenceContractError(
                    f"parent cycle detected involving {start!r}")
            walked.add(node)
            node = parents[node]


def canonical_result_hash(payload) -> str:
    """Stable hash of the canonical validated output (sort_keys, no whitespace)."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Bounded reference repair (candidate Arm C; never wired in by default)
# ---------------------------------------------------------------------------
# Scope ceiling: contract-compliance only. A repair step NEVER decides which
# interests exist, what they mean, or which evidence supports them — it may
# only constrain dangling name references to names actually present in the
# same payload. Optional relationship edges (related_to /
# regret.related_interests) may be dropped deterministically because losing
# an edge preserves the referenced objects' meaning. Required references
# (questions.interest, parent) go through ONE bounded provider repair round
# against an explicit valid-name list; anything beyond that fails closed.
# Every mutation returns a receipt line. Enum/bounds/shape violations are
# NOT repairable: with a strict output schema they should be impossible, so
# their presence signals provider/schema enforcement failure.

REFERENCE_REPAIRABLE_MARKERS = (
    "related_to target not in returned interests",
    "related_interests target not in returned interests",
    "parent not in returned interests",
    "interest not in returned interests",
)
MAX_REFERENCE_REPAIR_ATTEMPTS = 2


def classify_contract_error(exc: Exception) -> str:
    """'reference' if the violation is repairable-by-reference-only."""
    text = str(exc)
    for marker in REFERENCE_REPAIRABLE_MARKERS:
        if marker in text:
            return "reference"
    return "other"


def deterministic_reference_hygiene(payload: dict) -> tuple[dict, list]:
    """Drop dangling optional-edge references; everything else untouched.

    Returns (possibly-new payload, receipts). The payload is deep-copied;
    input is never mutated.
    """
    import copy
    cleaned = copy.deepcopy(payload)
    receipts: list = []
    names = {_norm_name(it["name"]) for it in cleaned["inferred_interests"]}

    for i, it in enumerate(cleaned["inferred_interests"]):
        kept = [t for t in it.get("related_to", [])
                if _norm_name(t) in names]
        dropped = [t for t in it.get("related_to", [])
                   if _norm_name(t) not in names]
        if dropped:
            it["related_to"] = kept
            for d in dropped:
                receipts.append({"repair_type": "drop_dangling_related_to",
                                 "container": f"inferred_interests[{i}]",
                                 "dropped_target": d})
    for i, rc in enumerate(cleaned["regret_candidates"]):
        kept = [t for t in rc.get("related_interests", [])
                if _norm_name(t) in names]
        dropped = [t for t in rc.get("related_interests", [])
                   if _norm_name(t) not in names]
        if dropped:
            rc["related_interests"] = kept
            for d in dropped:
                receipts.append({
                    "repair_type": "drop_dangling_regret_related_interest",
                    "container": f"regret_candidates[{i}]",
                    "dropped_target": d})
    return cleaned, receipts


REPAIR_PROMPT_TEMPLATE = """You previously produced the JSON payload below. Mechanical validation found these exact referential defects:

{errors}

Valid interest names in this payload (authoritative, normalized case-insensitively):

{valid_names}

Repair ONLY the defective reference strings so they point at names from the valid list (fix casing/spelling to match an existing name, or repoint). Do NOT add, remove, rename, re-order, or reword any interest, question, regret candidate, evidence summary, cluster id, confidence value, kind, temporal_state, stance, or observed_vs_inferred. Do NOT drop items. Return ONLY the complete corrected JSON payload matching the same schema.

PAYLOAD:

{payload}
"""


def build_repair_prompt(payload: dict, errors: list[str]) -> str:
    names = sorted({_norm_name(it["name"])
                    for it in payload["inferred_interests"]})
    return REPAIR_PROMPT_TEMPLATE.format(
        errors="\n".join(f"- {e}" for e in errors),
        valid_names="\n".join(f"- {n}" for n in names),
        payload=json.dumps(payload, indent=1, ensure_ascii=False))


def validated_reference_repair(payload: dict, supplied_cluster_ids,
                               invoke, *, max_attempts: int =
                               MAX_REFERENCE_REPAIR_ATTEMPTS):
    """Boundedly repair reference-only contract violations via the provider.

    invoke(prompt: str) -> dict must run the provider with the SAME strict
    output schema applied. Returns (payload, receipts, attempts_used).
    Raises InferenceContractError when the violation is outside the repair
    scope or attempts are exhausted — the caller then fails closed.

    Hard gates, both fail-closed: the incoming payload must be
    structurally schema-clean (shape violations are enforcement failures,
    never repairable), and the repaired payload must preserve the exact
    normalized interest-name inventory (a repair may never decide which
    interests exist).
    """
    schema_errs = conformance_errors(payload, inference_output_schema())
    if schema_errs:
        raise InferenceContractError(
            "repair refused: payload is structurally schema-invalid "
            "(provider/schema enforcement failure, not a reference "
            f"defect): {schema_errs[:3]}")
    receipts: list = []
    supplied = set(supplied_cluster_ids)
    try:
        validate_inference(payload, supplied)
        return payload, receipts, 0
    except InferenceContractError as exc:
        if classify_contract_error(exc) != "reference":
            raise
        errors = [f"{exc}"]
        receipts.append({"repair_type": "validation_error",
                         "error": str(exc)[:STDERR_DIAGNOSTIC_LIMIT]})

    original_names = {_norm_name(it["name"])
                      for it in payload["inferred_interests"]}
    current = payload
    for attempt in range(1, max_attempts + 1):
        prompt = build_repair_prompt(current, errors)
        try:
            candidate = invoke(prompt)
        except ProviderExecutionError:
            raise
        except InferenceContractError as exc:
            raise InferenceContractError(
                f"repair attempt {attempt} produced a non-reference "
                f"violation; refusing to continue: {exc}") from exc
        repaired_names = {_norm_name(it["name"])
                          for it in candidate.get("inferred_interests", [])} \
            if isinstance(candidate, dict) else set()
        if repaired_names != original_names:
            raise InferenceContractError(
                f"repair attempt {attempt} altered the interest "
                f"inventory (added={sorted(repaired_names -
                                           original_names)[:3]}, "
                f"removed={sorted(original_names -
                                  repaired_names)[:3]}); failing closed")
        try:
            validate_inference(candidate, supplied)
        except InferenceContractError as exc:
            if classify_contract_error(exc) != "reference":
                raise InferenceContractError(
                    f"repaired payload violates contract beyond reference "
                    f"scope; failing closed: {exc}") from exc
            errors = [str(exc)[:STDERR_DIAGNOSTIC_LIMIT]]
            receipts.append({"repair_type": "still_invalid_after_repair",
                             "attempt": attempt,
                             "error": errors[0]})
            current = candidate
            continue
        receipts.append({"repair_type": "reference_repair_applied",
                         "attempt": attempt})
        return candidate, receipts, attempt
    raise InferenceContractError(
        f"reference repair did not converge within {max_attempts} "
        "attempts; failing closed")


# ---------------------------------------------------------------------------
# Provider execution
# ---------------------------------------------------------------------------

def provider_command(provider: str, prompt_file: Path, prompt: str):
    """Build the provider command. Returns (cmd, requested_model).

    The model is what we REQUEST; this script does not verify which model
    actually served the request.
    """
    if provider == "codex":
        codex = shutil.which("codex")
        if not codex:
            raise ProviderExecutionError("codex not found on PATH")
        model = "gpt-5.6-luna"
        cmd = [codex, "exec", "--json", "--ephemeral", "-s", "read-only",
               "-m", model, "-c", "model_reasoning_effort=medium", "-C",
               "P:/",
               f"Read {prompt_file} and return ONLY the JSON. "
               "No prose, no markdown fences."]
    elif provider == "agy":
        model = "gemini/gemini-2.5-pro"
        cmd = ["agy", "-p", "--no-session", "--no-tools",
               "--model", model, prompt[:100000]]
    else:
        raise ValueError(f"unknown provider: {provider}")
    return cmd, model


def _invoke_and_extract(provider: str, prompt: str, prompt_file: Path,
                        timeout: int = 580):
    """Shared provider execution seam: returncode check, bounded stderr
    diagnostic, codex JSONL agent_message extraction, JSON extraction.
    Returns (parsed_json, requested_model) — the value is UNVALIDATED."""
    cmd, requested_model = provider_command(provider, prompt_file, prompt)

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       cwd="P:/", creationflags=creationflags)
    if r.returncode != 0:
        diag = (r.stderr or r.stdout or "").strip()
        raise ProviderExecutionError(
            f"provider {provider} exited {r.returncode}: "
            f"{diag[-STDERR_DIAGNOSTIC_LIMIT:]}")

    raw = r.stdout.strip()
    agent_text = extract_agent_message(raw)
    if agent_text:
        raw = agent_text
    return extract_json_object(raw), requested_model


def run_inference(provider: str = "codex", clusters=None, prompt_path=None,
                  result_path=None, timeout: int = 580,
                  run_root: Path | None = None):
    """LEGACY single-shot baseline: one global top-25 breadth-ranked subset.

    Returns (payload, meta) where meta carries the inference-run
    provenance consumed by personal_graph.store_validated_inference.
    Raises ProviderExecutionError on subprocess/parse failure and
    InferenceContractError on contract violation — the canonical result
    artifact is written ONLY after successful validation.

    Artifacts are run-scoped: with no explicit overrides they land under a
    unique ARTIFACT_ROOT/runs/<run_id>/ directory; the retired fixed
    shared-temp result location is never read or written by any path.
    """
    if clusters is None:
        from ef.evidence_clusters import cached_clusters
        clusters, _coverage = cached_clusters()
    selected = clusters[:MAX_CLUSTERS]
    supplied = [int(c["cluster_id"]) for c in selected]
    prompt = build_prompt(selected)

    if run_root is None:
        run_root = _new_run_dir("single-shot")
    run_root = Path(run_root)
    prompt_file = Path(prompt_path) if prompt_path else \
        run_root / "prompt.txt"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")

    print(f"[inference] {len(selected)} clusters, "
          f"prompt {len(prompt):,} chars -> {provider}")
    payload, requested_model = _invoke_and_extract(
        provider, prompt, prompt_file, timeout)

    validate_inference(payload, set(supplied))
    result_hash = canonical_result_hash(payload)

    out = Path(result_path) if result_path else \
        run_root / "result.validated.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_json(out, {
        "run_id": run_root.name,
        "validation_status": "validated",
        "payload": payload,
        "result_hash": result_hash,
    })
    print(f"[inference] validated {len(payload['inferred_interests'])} "
          f"interests, {len(payload['questions'])} questions, "
          f"{len(payload['regret_candidates'])} regret candidates "
          f"-> {out}")

    meta = {
        "provider": provider,
        "requested_model": requested_model,
        "prompt_version": PROMPT_VERSION,
        "candidate_policy": CANDIDATE_POLICY,
        "cluster_ids": supplied,
        "result_hash": result_hash,
        "run_id": run_root.name,
    }
    return payload, meta


# ===========================================================================
# Full-coverage bounded bootstrap
# ===========================================================================
# The legacy path above sends ONE global top-25 breadth subset (the
# BASELINE). Bootstrap removes arbitrary candidate truncation: every
# mechanically eligible cluster is covered exactly once across bounded
# batches; batch outputs are validated FRAGMENTS, never canonical graph
# state; a bounded reconciliation tree merges fragments into one final
# V2 payload with an auditable disposition for every fragment. Failure
# at any stage means no persistence — never a best-effort partial graph.

class ReconciliationContractError(ValueError):
    """Reconciliation output violated the fragment-disposition contract."""


def fragment_identity_id(plan_id: str, batch_id: str, interest_name,
                         cluster_ids) -> str:
    payload = "\x1f".join((plan_id, batch_id, _norm_name(interest_name),
                           ",".join(str(c) for c in sorted(cluster_ids))))
    return "frag_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_fragments(plan_id: str, batch_id: str, payload: dict) -> dict:
    """Deterministic fragment records for one validated batch payload."""
    interests = []
    for it in payload["inferred_interests"]:
        interests.append({
            "fragment_id": fragment_identity_id(
                plan_id, batch_id, it["name"], it["cluster_ids"]),
            "batch_id": batch_id,
            "interest": it,
            "cluster_ids": list(it["cluster_ids"]),
        })
    return {
        "interests": interests,
        "questions": [dict(q, batch_id=batch_id)
                      for q in payload["questions"]],
        "regret_candidates": [dict(rc, batch_id=batch_id)
                              for rc in payload["regret_candidates"]],
    }


def run_batch_inference(plan_id, batch, batch_clusters, provider="codex",
                        timeout: int = 580, prompt_path=None):
    """One bounded batch: prompt from EXACTLY this batch's clusters,
    validation against EXACTLY this batch's supplied cluster ids.

    Returns (fragments, batch_meta). NEVER persists anything — batch
    outputs are intermediate validated fragments only."""
    supplied = [int(c["cluster_id"]) for c in batch_clusters]
    if sorted(batch.cluster_ids) != sorted(supplied):
        raise ValueError(
            f"batch {batch.batch_id}: hydrated clusters {sorted(supplied)} "
            f"do not match plan ids {sorted(batch.cluster_ids)}")
    prompt = build_prompt(batch_clusters)
    if prompt_path is None:
        # artifact-hygiene rule: unique per-call home, never a fixed
        # shared P:/tmp location that concurrent runs could clobber
        prompt_path = _new_run_dir(
            f"batch-{batch.batch_id}") / "prompt.txt"
    prompt_file = Path(prompt_path)
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")

    print(f"[batch {batch.batch_id}] {len(supplied)} clusters, "
          f"prompt {len(prompt):,} chars -> {provider}")
    parsed, requested_model = _invoke_and_extract(
        provider, prompt, prompt_file, timeout)
    validate_inference(parsed, set(supplied))
    fragments = build_fragments(plan_id, batch.batch_id, parsed)
    meta = {
        "batch_id": batch.batch_id,
        "provider": provider,
        "requested_model": requested_model,
        "prompt_version": PROMPT_VERSION,
        "cluster_ids": supplied,
        "result_hash": canonical_result_hash(parsed),
    }
    return fragments, meta


RECONCILIATION_PROMPT_TEMPLATE = """You are reconciling inferred-interest fragments produced by independent batch analyses of one person's evidence clusters. The batches could not see each other, so equivalent interests may appear multiple times under different names.

Fragments (JSON):
{fragments}

Reconcile them into ONE final result:
- merge semantically equivalent interests across batches (combine their cluster_ids, keep the strongest evidence, note counterevidence)
- discover cross-batch latent goal relationships (parent/related_to)
- preserve unique well-supported interests
- deduplicate questions; keep regret candidates that remain implied
- every final interest may cite only cluster ids present in the fragments assigned to it

Return ONLY valid JSON:
{{
  "final": {{
    "inferred_interests": [ ...same interest schema as the fragments... ],
    "questions": [ {{"text": "...", "interest": "...", "status": "open|watching"}} ],
    "regret_candidates": [ ...same regret schema as the fragments... ]
  }},
  "fragment_dispositions": [
    {{"fragment_id": "...", "decision": "kept|merged|discarded",
      "target_interest": "final interest name or null",
      "reason": "non-empty explanation"}}
  ]
}}

Hard constraints:
- EVERY input fragment_id appears exactly once in fragment_dispositions.
- kept/merged require target_interest naming a final interest.
- A fragment may be discarded ONLY for evidence/noise/duplication reasons with a non-empty reason — NEVER because of output space or any count limit.
- Final cluster_ids must be supported by the fragments assigned to that interest.
"""


def _compact_fragments(group_fragments) -> str:
    compact = []
    for f in group_fragments:
        item = {"fragment_id": f["fragment_id"],
                "batch_id": f["batch_id"],
                "cluster_ids": list(f["cluster_ids"])}
        item.update(f["interest"])
        compact.append(item)
    return json.dumps(compact, ensure_ascii=False, indent=1)


def _reconcile_group(group_fragments, provider, timeout, prompt_file,
                     invoke=None):
    """One reconciliation provider call over one bounded group."""
    prompt = RECONCILIATION_PROMPT_TEMPLATE.format(
        fragments=_compact_fragments(group_fragments))
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")
    fn = invoke or _invoke_and_extract
    parsed, _model = fn(provider, prompt, prompt_file, timeout)
    if not isinstance(parsed, dict) or "final" not in parsed or \
            "fragment_dispositions" not in parsed:
        raise ReconciliationContractError(
            "reconciliation output must contain 'final' and "
            f"'fragment_dispositions', got keys "
            f"{list(parsed) if isinstance(parsed, dict) else type(parsed).__name__}")
    return parsed


def validate_reconciliation(wrapper, stage_fragments, allowed_cluster_ids):
    """Mechanical reconciliation contract — fail closed.

    - every input fragment dispositioned exactly once; no unknown ids
    - decisions valid; kept/merged resolve to final interests
    - discarded requires a non-empty reason
    - final payload passes the full V2 contract against the plan universe
    - each final interest's cluster_ids are supported by the fragments
      assigned to it (no invented evidence)
    """
    if not isinstance(wrapper, dict):
        raise ReconciliationContractError(
            f"reconciliation wrapper must be an object, got "
            f"{type(wrapper).__name__}")
    dispositions = wrapper.get("fragment_dispositions")
    if not isinstance(dispositions, list):
        raise ReconciliationContractError(
            "fragment_dispositions must be a list")
    input_ids = {f["fragment_id"] for f in stage_fragments}
    clusters_by_id = {f["fragment_id"]: set(f["cluster_ids"])
                      for f in stage_fragments}

    seen: dict = {}
    for d in dispositions:
        if not isinstance(d, dict):
            raise ReconciliationContractError("disposition must be an object")
        fid = d.get("fragment_id")
        if fid not in input_ids:
            raise ReconciliationContractError(
                f"disposition references unknown fragment {fid!r}")
        if fid in seen:
            raise ReconciliationContractError(
                f"fragment {fid} dispositioned twice")
        seen[fid] = d

    missing = sorted(input_ids - set(seen))
    if missing:
        raise ReconciliationContractError(
            f"fragments silently dropped (no disposition): {missing[:5]}")

    final = wrapper.get("final")
    if not isinstance(final, dict):
        raise ReconciliationContractError("'final' must be an object")
    final_names = {_norm_name(it["name"])
                   for it in final.get("inferred_interests", [])}
    supported = {}
    for fid, d in seen.items():
        decision = d.get("decision")
        if decision not in ("kept", "merged", "discarded"):
            raise ReconciliationContractError(
                f"fragment {fid}: invalid decision {decision!r}")
        reason = d.get("reason")
        target = d.get("target_interest")
        if decision == "discarded":
            if not isinstance(reason, str) or not reason.strip():
                raise ReconciliationContractError(
                    f"fragment {fid}: discarded without a non-empty reason")
        else:
            if _norm_name(target) not in final_names:
                raise ReconciliationContractError(
                    f"fragment {fid}: target_interest {target!r} does not "
                    f"resolve to a final interest")
            supported.setdefault(_norm_name(target), set()).update(
                clusters_by_id[fid])

    validate_inference(final, set(allowed_cluster_ids))
    for it in final["inferred_interests"]:
        allowed = supported.get(_norm_name(it["name"]), set())
        unsupported = set(it["cluster_ids"]) - allowed
        if unsupported:
            raise ReconciliationContractError(
                f"final interest {it['name']!r} cites cluster ids not "
                f"supported by its assigned fragments: {sorted(unsupported)}")
    return wrapper


def reconciliation_stage_structure(n_fragments: int,
                                   max_per_call: int =
                                   MAX_FRAGMENTS_PER_RECONCILIATION):
    """Worst-case shape of the bounded reconciliation tree: one list per
    stage, each a list of group sizes, ASSUMING each stage's outputs feed
    the next stage. Real depth depends on how much merging each stage
    does; a stage that fails to reduce its input count fails closed
    (see run_reconciliation_tree) rather than looping or truncating."""
    levels = []
    current = n_fragments
    if current <= 0:
        return levels
    while True:
        n_groups = -(-current // max_per_call)
        sizes = [min(max_per_call, current - i * max_per_call)
                 for i in range(n_groups)]
        levels.append(sizes)
        if n_groups == 1:
            return levels
        current = n_groups


def run_reconciliation_tree(fragments, plan_cluster_ids, provider="codex",
                            timeout: int = 580, prompt_path=None,
                            invoke=None, stage_writer=None,
                            max_per_call: int =
                            MAX_FRAGMENTS_PER_RECONCILIATION,
                            repair_hook=None):
    """Bounded recursive reconciliation over ALL fragments.

    Returns {"final", "fragment_dispositions" (flattened to LEAF
    fragments), "stages" (raw per-stage records), "provider_calls"}.
    No arbitrary truncation: every leaf fragment retains an auditable
    disposition chain to the final result.

    repair_hook (default off): candidate Arm C only. Called as
    hook(wrapper, group_fragments, stage=N, group_index=M) AFTER the
    provider returns and BEFORE mechanical validation. Scope ceiling:
    reference-only repair of ``wrapper["final"]``; fragment dispositions
    are audit records and must never be altered by a hook. Hooks are part
    of the contract-reliability experiment surface — no production caller
    passes one until that gate is decided.
    """
    leaf_records = sorted(fragments["interests"],
                          key=lambda f: f["fragment_id"])
    stage_records = []
    current = leaf_records
    final_wrapper = None
    provider_calls = 0
    stage = 0
    # Per-tree prompt isolation: when the caller supplies no path, every
    # group call gets its own file under a fresh run directory — no
    # cross-run or intra-run overwrites (artifact-hygiene rule).
    recon_prompt_root = _new_run_dir("recon") if prompt_path is None else None
    while True:
        stage += 1
        groups = [current[i:i + max_per_call]
                  for i in range(0, len(current), max_per_call)]
        is_last = len(groups) == 1
        record = {"stage": stage, "group_sizes": [len(g) for g in groups],
                  "dispositions": [], "outputs": {}}
        next_current = []
        for gi, group in enumerate(groups):
            prompt_file = Path(prompt_path) if prompt_path else \
                recon_prompt_root / f"s{stage}-g{gi + 1:03d}-prompt.txt"
            wrapper = _reconcile_group(group, provider, timeout, prompt_file,
                                       invoke=invoke)
            provider_calls += 1
            if repair_hook is not None:
                wrapper = repair_hook(wrapper, group, stage=stage,
                                      group_index=gi + 1)
                if wrapper is None:
                    raise ReconciliationContractError(
                        "repair_hook returned no wrapper")
            # Validation ALWAYS runs after any hook: a repair can never
            # substitute for the mechanical contract check.
            validate_reconciliation(wrapper, group, plan_cluster_ids)
            record["dispositions"].extend(wrapper["fragment_dispositions"])
            if is_last:
                final_wrapper = wrapper
            else:
                for it in wrapper["final"]["inferred_interests"]:
                    nid = fragment_identity_id(
                        f"recon-s{stage}", f"g{gi + 1:03d}", it["name"],
                        it["cluster_ids"])
                    record["outputs"][_norm_name(it["name"])] = nid
                    next_current.append({
                        "fragment_id": nid,
                        "batch_id": f"recon-s{stage}-g{gi + 1:03d}",
                        "interest": it,
                        "cluster_ids": list(it["cluster_ids"]),
                    })
        stage_records.append(record)
        if stage_writer:
            stage_writer(stage, record)
        if is_last:
            break
        if len(next_current) >= len(current):
            raise ReconciliationContractError(
                f"reconciliation stage {stage} did not reduce the fragment "
                f"count ({len(current)} -> {len(next_current)}); the "
                f"bounded tree cannot converge without truncation — "
                f"failing closed")
        if stage >= MAX_RECONCILIATION_STAGES:
            raise ReconciliationContractError(
                f"reconciliation exceeded {MAX_RECONCILIATION_STAGES} "
                f"stages; failing closed rather than truncating")
        current = sorted(next_current, key=lambda f: f["fragment_id"])

    leaf_dispositions = _flatten_leaf_dispositions(stage_records,
                                                   leaf_records)
    return {"final": final_wrapper["final"],
            "fragment_dispositions": leaf_dispositions,
            "stages": stage_records,
            "provider_calls": provider_calls}


def _flatten_leaf_dispositions(stage_records, leaf_records):
    """Walk every leaf fragment through the stage chain to its terminal
    disposition. A leaf discarded at any stage is terminal-discarded;
    otherwise the final stage's target_interest is the terminal result."""
    if len(stage_records) == 1:
        return stage_records[0]["dispositions"]
    out = []
    for leaf in leaf_records:
        cur = leaf["fragment_id"]
        decision = target = reason_chain = None
        for si, rec in enumerate(stage_records):
            disp = {d["fragment_id"]: d for d in rec["dispositions"]}.get(cur)
            if disp is None:
                raise ReconciliationContractError(
                    f"fragment {cur} has no disposition at stage "
                    f"{si + 1} during lineage flattening")
            if disp["decision"] == "discarded":
                decision, target = "discarded", None
                reason_chain = [disp["reason"]]
                break
            nxt = None
            if si + 1 < len(stage_records):
                nxt = stage_records[si]["outputs"].get(
                    _norm_name(disp["target_interest"]))
            if nxt is None:
                decision = disp["decision"]
                target = disp["target_interest"]
                reason_chain = [disp["reason"]]
                break
            reason_chain = [disp["reason"]]
            cur = nxt
        out.append({"fragment_id": leaf["fragment_id"],
                    "decision": decision,
                    "target_interest": target,
                    "reason": "; ".join(r for r in reason_chain if r)})
    return out


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def run_bootstrap(provider="codex", allow_spend=False, artifact_root=None,
                  timeout: int = 580, store=False, inventory=None,
                  hydrate=None, invoke=None):
    """Execute the full-coverage bounded bootstrap end to end.

    Fail-closed: any batch/reconciliation/validation failure marks the
    run failed and NOTHING is persisted. Only with store=True is the
    single final reconciled payload persisted through the existing
    transactional path. Batch fragments are never persisted canonically.
    """
    if not allow_spend:
        raise PermissionError(
            "multi-call bootstrap requires explicit --allow-spend")
    from ef.evidence_clusters import evidence_cluster_inventory, \
        hydrate_evidence_clusters
    from ef.interest_candidates import build_bootstrap_plan, \
        validate_plan_coverage

    if inventory is None:
        inventory = evidence_cluster_inventory()
    plan = build_bootstrap_plan(
        inventory["clusters"],
        max_per_call=BOOTSTRAP_MAX_CLUSTERS_PER_CALL,
        exclusions=inventory.get("exclusions", {}))
    validate_plan_coverage(plan, BOOTSTRAP_MAX_CLUSTERS_PER_CALL)

    run_dir = Path(artifact_root) if artifact_root else (
        ARTIFACT_ROOT / f"{time.strftime('%Y%m%dT%H%M%S')}_"
                        f"{uuid.uuid4().hex[:8]}_{plan.plan_id}")
    _write_json(run_dir / "plan.json", plan.to_dict())
    _write_json(run_dir / "inventory-summary.json", {
        "total_semantic_non_series":
            inventory.get("total_semantic_non_series"),
        "eligible_count": inventory.get("eligible_count"),
        "exclusions": inventory.get("exclusions", {}),
    })

    hydrate_fn = hydrate or hydrate_evidence_clusters
    all_fragments = {"interests": [], "questions": [],
                     "regret_candidates": []}
    provider_calls = 0
    requested_model = None
    try:
        for i, batch in enumerate(plan.batches, 1):
            packets = hydrate_fn(list(batch.cluster_ids))
            fragments, meta = run_batch_inference(
                plan.plan_id, batch, packets, provider=provider,
                timeout=timeout,
                prompt_path=run_dir / "prompts" /
                            f"{batch.batch_id}-prompt.txt")
            provider_calls += 1
            requested_model = meta["requested_model"]
            _write_json(run_dir / f"batch-{i:02d}-input-metadata.json", {
                "batch_id": batch.batch_id,
                "cluster_ids": list(batch.cluster_ids)})
            _write_json(run_dir / f"batch-{i:02d}-validated-result.json",
                        {"meta": meta, "fragments": fragments})
            all_fragments["interests"].extend(fragments["interests"])
            all_fragments["questions"].extend(fragments["questions"])
            all_fragments["regret_candidates"].extend(
                fragments["regret_candidates"])

        recon = run_reconciliation_tree(
            all_fragments, list(plan.eligible_cluster_ids), provider,
            timeout, invoke=invoke,
            stage_writer=lambda s, rec: _write_json(
                run_dir / f"reconciliation-stage-{s:02d}.json", rec))
        provider_calls += recon["provider_calls"]
        validate_inference(recon["final"], set(plan.eligible_cluster_ids))
        _write_json(run_dir / "final-validated-result.json", recon)

        summary = {
            "status": "success", "plan_id": plan.plan_id,
            "policy": plan.policy, "provider": provider,
            "provider_calls": provider_calls,
            "batches": len(plan.batches),
            "eligible_clusters": len(plan.eligible_cluster_ids),
            "fragments": len(all_fragments["interests"]),
            "final_interests": len(recon["final"]["inferred_interests"]),
            "dispositions": len(recon["fragment_dispositions"]),
        }
        _write_json(run_dir / "run-summary.json", summary)
    except Exception as exc:
        summary = {
            "status": "failed", "plan_id": plan.plan_id,
            "policy": plan.policy, "provider": provider,
            "provider_calls": provider_calls,
            "error_type": type(exc).__name__,
            "error": str(exc)[:STDERR_DIAGNOSTIC_LIMIT],
        }
        _write_json(run_dir / "run-summary.json", summary)
        raise

    if store:
        from ef.personal_graph import connect, store_validated_inference
        run_id = (f"run_{time.strftime('%Y%m%dT%H%M%S')}_"
                  f"{canonical_result_hash(recon['final'])[:8]}")
        conn = connect()
        try:
            store_validated_inference(
                conn, recon["final"], run_id=run_id, provider=provider,
                model=requested_model or provider,
                prompt_version=PROMPT_VERSION,
                candidate_policy=plan.policy,
                cluster_ids=list(plan.eligible_cluster_ids),
                result_hash=canonical_result_hash(recon["final"]))
        finally:
            conn.close()
    return {"run_dir": str(run_dir), "summary": summary,
            "final": recon["final"],
            "fragment_dispositions": recon["fragment_dispositions"]}


def _plan_artifact_dir(artifact_root, plan_id) -> Path:
    root = Path(artifact_root) if artifact_root else ARTIFACT_ROOT
    d = root / f"{time.strftime('%Y%m%dT%H%M%S')}_{plan_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(argv=None) -> int:
    from csf.paths import load_workspace_env
    load_workspace_env()
    ap = argparse.ArgumentParser(
        description="Interest inference. Plain invocation is the LEGACY "
                    "single-shot top-25 baseline, not the authoritative "
                    "bootstrap; use --run-bootstrap for full coverage.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provider", default="codex")
    ap.add_argument("--store", action="store_true",
                    help="store results in the typed tables")
    ap.add_argument("--plan-bootstrap", action="store_true",
                    help="write the full-coverage bootstrap plan (zero "
                         "provider calls)")
    ap.add_argument("--plan-baseline", action="store_true",
                    help="write the legacy top-25 baseline plan (zero "
                         "provider calls)")
    ap.add_argument("--run-bootstrap", action="store_true",
                    help="execute all planned bootstrap batches plus "
                         "reconciliation (requires --allow-spend)")
    ap.add_argument("--allow-spend", action="store_true",
                    help="authorize multi-call provider execution")
    ap.add_argument("--artifact-dir", default=None,
                    help="override runtime artifact root (tests)")
    a = ap.parse_args(argv)

    if a.plan_bootstrap or a.plan_baseline:
        from ef.evidence_clusters import evidence_cluster_inventory
        from ef.interest_candidates import build_baseline_plan, \
            build_bootstrap_plan
        inventory = evidence_cluster_inventory()
        if a.plan_bootstrap:
            plan = build_bootstrap_plan(
                inventory["clusters"],
                max_per_call=BOOTSTRAP_MAX_CLUSTERS_PER_CALL,
                exclusions=inventory.get("exclusions", {}))
        else:
            plan = build_baseline_plan(
                inventory["clusters"], exclusions=inventory.get(
                    "exclusions", {}))
        out = _plan_artifact_dir(a.artifact_dir, plan.plan_id)
        _write_json(out / "plan.json", plan.to_dict())
        _write_json(out / "inventory-summary.json", {
            "total_semantic_non_series":
                inventory.get("total_semantic_non_series"),
            "eligible_count": inventory.get("eligible_count"),
            "exclusions": inventory.get("exclusions", {}),
        })
        print(f"[plan] {plan.policy} {plan.plan_id}: "
              f"{plan.metrics.planned_count}/{plan.metrics.eligible_count} "
              f"clusters, {plan.metrics.batch_count} batches "
              f"(max {plan.metrics.max_batch_size}) -> {out / 'plan.json'}")
        return 0

    if a.run_bootstrap:
        result = run_bootstrap(provider=a.provider, allow_spend=a.allow_spend,
                               artifact_root=a.artifact_dir, store=a.store)
        print(json.dumps(result["summary"], indent=2))
        return 0

    if a.dry_run:
        from ef.evidence_clusters import cached_clusters
        clusters, coverage = cached_clusters()
        packets = build_packets(clusters)
        print(packets[:3000])
        print(f"\n[dry-run] {len(clusters)} clusters, "
              f"{len(packets):,} chars of packets")
        return 0

    payload, meta = run_inference(a.provider)
    if a.store:
        from ef.personal_graph import connect, store_validated_inference
        run_id = (f"run_{time.strftime('%Y%m%dT%H%M%S')}_"
                  f"{meta['result_hash'][:8]}")
        conn = connect()
        try:
            summary = store_validated_inference(
                conn, payload, run_id=run_id, provider=meta["provider"],
                model=meta["requested_model"],
                prompt_version=meta["prompt_version"],
                candidate_policy=meta["candidate_policy"],
                cluster_ids=meta["cluster_ids"],
                result_hash=meta["result_hash"])
        finally:
            conn.close()
        print(f"[stored] {summary}")
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False)[:2000])
        print(f"\n[meta] {json.dumps(meta)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
