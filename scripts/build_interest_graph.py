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
  - MAX_CLUSTERS stays 25 with breadth-biased selection. The known
    recall limitation is NOT fixed here — the next packet handles
    candidate selection.

Usage:
    python scripts/build_interest_graph.py --dry-run    # show packets
    python scripts/build_interest_graph.py              # run inference
    python scripts/build_interest_graph.py --provider agy  # override
    python scripts/build_interest_graph.py --store      # persist typed graph
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MAX_CLUSTERS = 25          # packets sent per inference call (breadth-biased
                           # selection — KNOWN recall limitation, deliberately
                           # NOT tuned by the contract-fidelity packet)
MAX_REPS_PER_CLUSTER = 4   # representative docs per packet
PROMPT_VERSION = "v2.1-contract-fidelity"
CANDIDATE_POLICY = f"top{MAX_CLUSTERS}-breadth-biased"
STDERR_DIAGNOSTIC_LIMIT = 2000

KINDS = ("domain", "topic", "subtopic", "method", "monitor")
TEMPORAL_STATES = ("durable", "active", "current_problem", "episodic",
                   "emerging", "dormant")
STANCES = ("curiosity", "learning", "project", "monitoring", "entertainment")
OBSERVED_VS_INFERRED = ("observed", "inferred", "inferred_adjacent")
QUESTION_STATUSES = ("open", "watching")
REGRET_LABELS = ("inferred_adjacent",)


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


def run_inference(provider: str = "codex", clusters=None, prompt_path=None,
                  result_path=None, timeout: int = 580):
    """Run one inference invocation: packets → provider → validation.

    Returns (payload, meta) where meta carries the inference-run
    provenance consumed by personal_graph.store_validated_inference.
    Raises ProviderExecutionError on subprocess/parse failure and
    InferenceContractError on contract violation — the canonical result
    artifact is written ONLY after successful validation.
    """
    if clusters is None:
        from ef.evidence_clusters import cached_clusters
        clusters, _coverage = cached_clusters()
    selected = clusters[:MAX_CLUSTERS]
    supplied = [int(c["cluster_id"]) for c in selected]
    prompt = build_prompt(selected)

    prompt_file = Path(prompt_path) if prompt_path else \
        Path("P:/tmp/interest-inference-prompt.txt")
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")

    print(f"[inference] {len(selected)} clusters, "
          f"prompt {len(prompt):,} chars -> {provider}")
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
    payload = extract_json_object(raw)

    validate_inference(payload, set(supplied))
    result_hash = canonical_result_hash(payload)

    out = Path(result_path) if result_path else \
        Path("P:/tmp/interest-inference-result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                   encoding="utf-8")
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
    }
    return payload, meta


def main(argv=None) -> int:
    from csf.paths import load_workspace_env
    load_workspace_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--provider", default="codex")
    ap.add_argument("--store", action="store_true",
                    help="store results in the typed tables")
    a = ap.parse_args(argv)

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
