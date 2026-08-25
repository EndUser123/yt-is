"""Retrospective concept-discovery evaluator — frozen machinery.

Contamination architecture (2026-08-25 amendment): this evaluator is
FROZEN before any formal holdout is opened. Target labels are post-hoc
SCORING inputs only — they never enter discovery inputs, thresholds,
queries, metric selection, or policy. The formal gate runs in a separate
contamination-isolated evaluator lane against a holdout THIS implementer
context has never seen. The six technology names exposed to the
implementer context on 2026-08-24 are CONTAMINATED and may only be used
as explicitly-labeled NON-BLIND_DIAGNOSTIC plumbing cases — never as
promotion evidence.

Frozen order of operations (B2/B4/B5):
  1. `freeze`   — record production commit/file hashes + all policy
                  hashes; write frozen-code-hashes.json. No targets read.
  2. `run`      — REFUSES to load targets without a valid freeze receipt;
                  verifies on-disk production files match the receipt
                  (fail closed on drift); replays discovery blind (names
                  never in inputs — only as_of cutoffs), then computes
                  per-target scorability, checkpoint replays, metrics,
                  negative controls, perturbations, baselines.

Usage:
    python scripts/evaluate_concept_discovery.py freeze \
        [--receipt-dir P:/.data/yt-is/ef/concept-discovery-eval/<id>]
    python scripts/evaluate_concept_discovery.py run \
        --receipt <frozen-code-hashes.json> --targets <private.json> \
        [--label NON-BLIND_DIAGNOSTIC] [--max-checkpoint-targets N]
    python scripts/evaluate_concept_discovery.py calibrate [--receipt ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# ===========================================================================
# FROZEN POLICY STRUCTURES — hashed into the freeze receipt. Changing any
# of these after freeze invalidates the receipt (fail closed at run time).
# ===========================================================================

EVALUATOR_VERSION = "retrospective-evaluator-v2"
ARTIFACT_SCHEMA_VERSION = "concept-discovery-eval-v2"

CHECKPOINT_OFFSETS = [(-30, "T-30"), (0, "T"), (7, "T+7"),
                      (14, "T+14"), (30, "T+30"), (60, "T+60")]

FORMAL_LABEL = "FORMAL"   # exact match only; aliases never consume authority
SINGLE_USE_POLICY = {
    "version": "single-use-holdout-v1",
    "ledger_path": "P:/.data/yt-is/private/formal-holdout-ledger.sqlite",
    "claim_rule": "sha256 of the exact holdout file bytes is atomically "
                  "claimed (INSERT, UNIQUE(holdout_sha256)) BEFORE target "
                  "labels are parsed; a duplicate claim fails closed "
                  "regardless of evaluator/policy generation",
    "crash_semantics": "a holdout is CONSUMED at successful claim; failure "
                       "after claim records FAILED_AFTER_CONSUMPTION and "
                       "the holdout remains consumed — no retry, a "
                       "replacement holdout is required",
    "non_formal": "only --label FORMAL (exact) consumes authority; "
                  "NON_BLIND_DIAGNOSTIC and synthetic runs never touch it",
    "ledger_fields": ["holdout_sha256", "freeze_receipt_sha256",
                      "evaluator_sha256", "production_generation", "run_id",
                      "claimed_at", "completed_at", "status",
                      "artifact_path"],
}

METRIC_PLAN = {
    "version": "metrics-v1",
    "target_metrics": ["candidate_recall_scorable", "emerging_recall_scorable",
                       "time_to_candidate_days", "time_to_emerging_days",
                       "lifecycle_by_checkpoint", "world_signal_percentile",
                       "source_diversity_satisfied"],
    "selectivity_metrics": ["candidates_per_checkpoint",
                            "emerging_fraction_per_checkpoint",
                            "world_signal_distribution",
                            "matched_negative_emerging_rate",
                            "candidate_to_emerging_selectivity"],
    "stability_metrics": ["adjacent_checkpoint_lifecycle_consistency",
                          "deterministic_replay_stability",
                          "ranking_stability_spearman"],
    "provenance_metrics": ["evidence_traceability_rows"],
    "definitions": {
        "candidate_recall_scorable":
            "matched candidates / scorable targets, at any checkpoint",
        "emerging_recall_scorable":
            "matched targets whose lifecycle reached emerging by T+60 / "
            "scorable targets",
        "time_to_candidate_days":
            "days between first qualifying evidence (T) and the earliest "
            "checkpoint whose replay contains a matched candidate",
        "matched_negative_emerging_rate":
            "emerging-classified matched negative controls / all matched "
            "negative controls at T (pre-window selection)",
        "percentile":
            "fraction of all candidates in the same replay with strictly "
            "lower world_signal_score",
    },
}

MATCHING_POLICY = {
    "version": "matching-v1",
    "rule": "normalized equality OR whole-word-boundary containment "
            "(casefold, punctuation-to-space, whitespace collapse) between "
            "candidate canonical_name/alias and target canonical_name/alias",
    "applies_after": "discovery outputs are produced; never before",
}

SCORABILITY_POLICY = {
    "version": "scorability-v1",
    "qualifying_evidence":
        "first date D such that >= 2 DISTINCT eu documents mentioning a "
        "target alias (via kg mentioned_in) exist on/before D; that D = T",
    "missing_evidence_verdict": "UNSCORABLE_MISSING_EVIDENCE",
}

NEGATIVE_CONTROL_POLICY = {
    "version": "negctl-v1",
    "select_from": "corpus entity concepts present in the T-30 replay "
                   "registry that do NOT match any target",
    "match_axes": {"evidence_count_ratio_max": 0.5,
                   "source_diversity_tolerance": 1},
    "per_target": 3,
    "determinism": "sort by concept_id ascending; take first per_target "
                   "satisfying the axes",
    "seed": "none needed (pure sort); recorded for policy completeness",
}

PERTURBATION_POLICY = {
    "version": "perturbation-v1",
    "removal_fractions": [0.10, 0.20],
    "unit": "one supporting eu observation row of the target in a TEMP "
            "catalog snapshot",
    "seed": "int(sha256(target_id)[:8], 16)",
    "checkpoint": "T+30",
    "production_catalog_mutation": "forbidden — snapshot copy only",
}

BASELINE_POLICIES = {
    "version": "baselines-v1",
    "A": {"name": "recent-absolute-count",
          "emerging_if": "recent_count >= 6"},
    "B": {"name": "recency-plus-count",
          "emerging_if": "recent_count >= 4 AND first_seen within 60d"},
}

VERDICT_RULES = {
    "version": "verdict-v2",
    "INSUFFICIENT_EVIDENCE": {
        "min_scorable_targets": 20,
        "min_matched_negative_controls": 40,
        "min_negatives_per_target": 2.0,
        "note": "preregistered 2026-08-25 BEFORE the unseen holdout is "
                "opened; must not be reduced after seeing it",
    },
    "PASS": {"candidate_recall_scorable_min": 0.7,
             "emerging_recall_scorable_min": 0.5,
             "matched_negative_emerging_rate_max": 0.2,
             "perturbation20_retention_min": 0.5,
             "must_beat_baselines": True},
    "PARTIAL": "recall OR selectivity good but the other axis fails the "
               "PASS bar without hitting FAIL",
    "FAIL": {"no_baseline_advantage": True,
             "or_matched_negative_emerging_rate_min": 0.5,
             "or_perturbation20_retention_max": 0.3},
    "note": "substantive thresholds carried unchanged from verdict-v1 "
            "(frozen 2026-08-25 before any formal holdout run); verdict-v2 "
            "adds the INSUFFICIENT_EVIDENCE sample-sufficiency gate and "
            "Wilson interval reporting",
}

UNCERTAINTY_POLICY = {
    "version": "uncertainty-v1",
    "method": "95% Wilson score intervals for proportion metrics",
    "applies_to": ["candidate_recall_scorable", "emerging_recall_scorable",
                   "matched_negative_emerging_rate",
                   "perturbation10_retention", "perturbation20_retention"],
    "note": "intervals report uncertainty; they do NOT replace the frozen "
            "point-estimate thresholds",
}

PRODUCTION_FILES = ("ef/concept_registry.py", "ef/concept_discovery.py",
                    "ef/horizon_scout.py", "scripts/discover_concepts.py",
                    "ef/evidence_clusters.py")

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
EVAL_ARTIFACT_ROOT = Path("P:/.data/yt-is/ef/concept-discovery-eval")


class FreezeError(RuntimeError):
    """Freeze receipt missing, stale, or production drifted from it."""


class HoldoutConsumedError(RuntimeError):
    """The formal holdout (by content hash) was already consumed by ANY
    prior formal claim, under ANY evaluator/policy generation. The holdout
    is permanently unusable; a replacement holdout is required."""


def _sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha256_obj(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _norm(text) -> str:
    text = re.sub(r"[^\w\s]", " ", str(text or "").casefold())
    return " ".join(text.split())


def _word_boundary_contains(hay: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(rf"\b{re.escape(needle)}\b", hay) is not None


# ---------------------------------------------------------------------------
# Single-use formal-holdout authority (private, outside git)
# ---------------------------------------------------------------------------

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS holdout_claims (
    holdout_sha256 TEXT PRIMARY KEY,
    freeze_receipt_sha256 TEXT NOT NULL,
    evaluator_sha256 TEXT NOT NULL,
    production_generation TEXT NOT NULL,
    run_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    artifact_path TEXT
);
"""


def ensure_holdout_ledger(ledger_path=None) -> None:
    p = Path(ledger_path or SINGLE_USE_POLICY["ledger_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(_LEDGER_DDL)
        conn.commit()
    finally:
        conn.close()


def claim_formal_holdout(ledger_path, holdout_sha256: str,
                         freeze_receipt_sha256: str, evaluator_sha256: str,
                         production_generation: str, run_id: str,
                         artifact_path: str = None) -> None:
    """Atomically claim a formal holdout by content hash BEFORE labels are
    parsed. UNIQUE(holdout_sha256) makes the claim globally single-use
    across evaluator generations: same holdout + same OR modified
    evaluator/policy are all forbidden after the first consumption."""
    ensure_holdout_ledger(ledger_path)
    conn = sqlite3.connect(str(ledger_path), timeout=30,
                           isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO holdout_claims (holdout_sha256, "
                "freeze_receipt_sha256, evaluator_sha256, "
                "production_generation, run_id, claimed_at, status, "
                "artifact_path) VALUES (?,?,?,?,?,?,?,?)",
                (holdout_sha256, freeze_receipt_sha256, evaluator_sha256,
                 production_generation, run_id,
                 time.strftime("%Y-%m-%dT%H:%M:%S"), "RUNNING",
                 artifact_path))
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            prior = conn.execute(
                "SELECT claimed_at, status, run_id FROM holdout_claims "
                "WHERE holdout_sha256=?", (holdout_sha256,)).fetchone()
            raise HoldoutConsumedError(
                f"formal holdout sha256 {holdout_sha256[:12]} was already "
                f"consumed (claimed_at={prior[0] if prior else '?'}, "
                f"status={prior[1] if prior else '?'}). A formal holdout "
                f"is permanently single-use across evaluator generations; "
                f"a replacement holdout is required.") from None
    finally:
        conn.close()


def mark_claim_status(ledger_path, holdout_sha256: str, status: str,
                      artifact_path: str = None) -> None:
    conn = sqlite3.connect(str(ledger_path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            "UPDATE holdout_claims SET status=?, completed_at=COALESCE("
            "completed_at, ?), artifact_path=COALESCE(?, artifact_path) "
            "WHERE holdout_sha256=?",
            (status, time.strftime("%Y-%m-%dT%H:%M:%S"),
             artifact_path, holdout_sha256))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Uncertainty (95% Wilson score intervals)
# ---------------------------------------------------------------------------

def wilson_interval(k: int, n: int) -> dict | None:
    """95% Wilson score interval for k successes in n trials."""
    if not n:
        return None
    z = 1.959963985
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return {"lo": round(max(0.0, center - half), 3),
            "hi": round(min(1.0, center + half), 3), "k": k, "n": n}


# ---------------------------------------------------------------------------
# Freeze
# ---------------------------------------------------------------------------

def production_commit_sha() -> str:
    import subprocess
    r = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise FreezeError(f"cannot resolve production commit: {r.stderr}")
    return r.stdout.strip()


def build_freeze_receipt(receipt_dir: Path, production_sha: str = None) -> dict:
    """Snapshot every hash the formal gate depends on. Reads NO targets."""
    from ef import concept_discovery as cd
    receipt = {
        "evaluator_version": EVALUATOR_VERSION,
        "artifact_schema": ARTIFACT_SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "production_commit": production_sha or production_commit_sha(),
        "production_file_sha256": {
            rel: _sha256_file(REPO / rel) for rel in PRODUCTION_FILES},
        "production_policy_version": cd.POLICY_VERSION,
        "production_policy_sha256": _sha256_obj(cd.POLICY),
        "evaluator_file_sha256": _sha256_file(Path(__file__)),
        "metric_plan_sha256": _sha256_obj(METRIC_PLAN),
        "matching_policy_sha256": _sha256_obj(MATCHING_POLICY),
        "scorability_policy_sha256": _sha256_obj(SCORABILITY_POLICY),
        "negative_control_policy_sha256": _sha256_obj(NEGATIVE_CONTROL_POLICY),
        "perturbation_policy_sha256": _sha256_obj(PERTURBATION_POLICY),
        "baseline_policies_sha256": _sha256_obj(BASELINE_POLICIES),
        "verdict_rules_sha256": _sha256_obj(VERDICT_RULES),
        "single_use_policy_sha256": _sha256_obj(SINGLE_USE_POLICY),
        "uncertainty_policy_sha256": _sha256_obj(UNCERTAINTY_POLICY),
        "formal_holdout_read": False,
    }
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "frozen-code-hashes.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def verify_frozen(receipt: dict, *, check_evaluator_hash: bool = True) -> None:
    """Fail closed if anything the receipt froze has drifted."""
    from ef import concept_discovery as cd
    for rel, expected in receipt["production_file_sha256"].items():
        actual = _sha256_file(REPO / rel)
        if actual != expected:
            raise FreezeError(
                f"production file {rel} drifted from frozen receipt "
                f"(expected {expected[:12]}, found {actual[:12]}) — "
                f"evaluation refuses to run")
    if check_evaluator_hash and \
            _sha256_file(Path(__file__)) != receipt["evaluator_file_sha256"]:
        # The evaluator itself may only change via a NEW freeze receipt.
        raise FreezeError("evaluator file drifted from its frozen hash")
    if cd.POLICY_VERSION != receipt["production_policy_version"] or \
            _sha256_obj(cd.POLICY) != receipt["production_policy_sha256"]:
        raise FreezeError("discovery policy drifted from frozen receipt")
    if receipt.get("formal_holdout_read"):
        raise FreezeError(
            "receipt already marked formal_holdout_read — formal gate must "
            "run in the contamination-isolated lane")


# ---------------------------------------------------------------------------
# Blind replay (names NEVER in inputs)
# ---------------------------------------------------------------------------

def replay_as_of(registry_path, as_of: str, catalog_path=None) -> dict:
    """One blind discovery replay. Inputs: paths + cutoff only."""
    from ef import concept_discovery
    from ef import concept_registry
    conn = concept_registry.connect(str(registry_path))
    try:
        return concept_discovery.scan_internal(
            conn, catalog_path=str(catalog_path or CATALOG), as_of=as_of)
    finally:
        conn.close()


def replay_series(registry_path, as_of_dates: list[str],
                  catalog_path=None) -> list[dict]:
    return [replay_as_of(registry_path, d, catalog_path)
            for d in as_of_dates]


# ---------------------------------------------------------------------------
# Post-hoc target handling (labels allowed ONLY below this line)
# ---------------------------------------------------------------------------

def load_targets(path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    targets = payload.get("targets", payload if isinstance(payload, list)
                          else [])
    for t in targets:
        for field in ("target_id", "canonical_name"):
            if not t.get(field):
                raise FreezeError(f"target missing {field}")
        t.setdefault("aliases", [])
    return targets


def first_qualifying_evidence(target: dict, catalog_path=None) -> str | None:
    """Post-hoc: first date by which >= 2 distinct eu docs mention an
    alias (cumulative). Uses the authoritative source-time field exactly
    like production."""
    conn = sqlite3.connect(
        f"file:{catalog_path or CATALOG}?mode=ro", uri=True, timeout=30)
    try:
        aliases = [target["canonical_name"]] + list(target["aliases"])
        for alias in aliases:
            rows = conn.execute(r"""
                SELECT substr(COALESCE(NULLIF(eu.published_at,''),
                                       eu.captured_at), 1, 10) d,
                       eu.eu_id
                FROM kg_edges m
                JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
                JOIN kg_nodes en ON en.node_id = m.src_id
                WHERE m.relation = 'mentioned_in'
                  AND lower(en.label) = lower(?)
                ORDER BY d""", (alias,)).fetchall()
            seen = set()
            for d, eu_id in rows:
                if d:
                    seen.add(eu_id)
                    if len(seen) >= 2:
                        return d
        return None
    finally:
        conn.close()


def _concept_names(registry_path) -> list[dict]:
    conn = sqlite3.connect(str(registry_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT concept_id, canonical_name, lifecycle_state, "
            "world_signal_score, evidence_count, source_diversity "
            "FROM concepts")]
    finally:
        conn.close()


def match_concept(concept_row: dict, target: dict) -> bool:
    names = [concept_row["canonical_name"]]
    t_names = [target["canonical_name"]] + list(target["aliases"])
    for cand in names:
        c = _norm(cand)
        for t in t_names:
            tn = _norm(t)
            if not tn or not c:
                continue
            if c == tn or _word_boundary_contains(c, tn) or \
                    _word_boundary_contains(tn, c):
                return True
    return False


def checkpoint_dates(t_date: str) -> list[tuple[str, str]]:
    import datetime
    base = datetime.date.fromisoformat(t_date)
    out = []
    for offset, label in CHECKPOINT_OFFSETS:
        d = base + datetime.timedelta(days=offset)
        out.append((label, min(d.isoformat(),
                               datetime.date.today().isoformat())))
    return out


# ---------------------------------------------------------------------------
# Evaluation pieces
# ---------------------------------------------------------------------------

def select_negative_controls(target_row: dict, candidates_t_minus_30,
                             matched_ids: set) -> list[str]:
    """Frozen policy: comparable pre-window evidence mass/diversity, no
    target match, deterministic order."""
    base = target_row.get("evidence_count", 0) or 0
    picks = []
    for row in sorted(candidates_t_minus_30, key=lambda r: r["concept_id"]):
        if row["concept_id"] in matched_ids:
            continue
        ec = row.get("evidence_count", 0) or 0
        if base and abs(ec - base) / max(base, 1) > \
                NEGATIVE_CONTROL_POLICY["match_axes"][
                    "evidence_count_ratio_max"]:
            continue
        picks.append(row["concept_id"])
        if len(picks) >= NEGATIVE_CONTROL_POLICY["per_target"]:
            break
    return picks


def snapshot_catalog(dest: Path, catalog_path=None) -> Path:
    src = Path(catalog_path or CATALOG)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def perturb_target_observations(snapshot: Path, target: dict,
                                fraction: float) -> int:
    """Delete `fraction` of the target's supporting eu observation rows in
    the TEMP snapshot. Deterministic per target_id."""
    rnd = int(hashlib.sha256(target["target_id"].encode()).hexdigest()[:8], 16)
    aliases = [target["canonical_name"]] + list(target["aliases"])
    conn = sqlite3.connect(str(snapshot))
    try:
        for alias in aliases:
            rows = conn.execute(r"""
                SELECT DISTINCT eu.eu_id FROM kg_edges m
                JOIN eu ON eu.eu_id = substr(m.dst_id, 4)
                JOIN kg_nodes en ON en.node_id = m.src_id
                WHERE m.relation = 'mentioned_in'
                  AND lower(en.label) = lower(?) ORDER BY eu.eu_id""",
                (alias,)).fetchall()
            if not rows:
                continue
            take = int(len(rows) * fraction)
            start = rnd % max(len(rows) - take, 1) if take else 0
            for (eu_id,) in rows[start:start + take]:
                conn.execute("DELETE FROM eu WHERE eu_id=?", (eu_id,))
            conn.commit()
            return take
        return 0
    finally:
        conn.close()


def baseline_emerging(features: dict, which: str) -> bool:
    if which == "A":
        return features.get("recent_count", 0) >= 6
    return features.get("recent_count", 0) >= 4 and \
        features.get("novel", False)


def _baseline_flags(features: dict) -> dict:
    return {"recent_count": features.get("recent_count", 0),
            "baseline_A": baseline_emerging(features, "A"),
            "baseline_B": baseline_emerging(features, "B"),
            "policy_emerging": features.get("policy_emerging", False)}


def _compare_baselines(rows: list[dict]) -> dict:
    """Target-vs-control emerging separation under the frozen policy and
    the two evaluator-only baselines (B9)."""
    def rate(kind, field):
        sub = [r for r in rows if r["kind"] == kind]
        return (sum(1 for r in sub if r[field]) / len(sub)) if sub else None

    def sep(field):
        t, c = rate("target", field), rate("control", field)
        if t is None or c is None:
            return None
        return round(t - c, 3)

    policy_sep = sep("policy_emerging")
    a_sep = sep("baseline_A")
    b_sep = sep("baseline_B")
    rivals = [s for s in (a_sep, b_sep) if s is not None]
    return {
        "policy_separation": policy_sep,
        "baseline_A_separation": a_sep,
        "baseline_B_separation": b_sep,
        "policy_beats_baselines": (
            policy_sep is not None and
            (not rivals or policy_sep >= max(rivals))) if rivals or
        policy_sep is not None else None,
        "n_rows": len(rows),
    }


def extract_features(registry_path, concept_id: str) -> dict:
    conn = sqlite3.connect(str(registry_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT world_signal_score, evidence_count, source_diversity, "
            "lifecycle_state, metadata_json FROM concepts WHERE concept_id=?",
            (concept_id,)).fetchone()
        if not row:
            return {"recent_count": 0, "baseline_count": 0, "novel": False,
                    "policy_emerging": False}
        meta = json.loads(row["metadata_json"]) if row["metadata_json"] \
            else {}
        return {"recent_count": meta.get("recent_count", 0),
                "baseline_count": meta.get("baseline_count", 0),
                "novel": meta.get("first_seen_days", 999) <= 60,
                "world_signal": row["world_signal_score"],
                "policy_emerging":
                    row["lifecycle_state"] == "emerging"}
    finally:
        conn.close()


def run_evaluation(receipt_path, targets_path, artifact_dir,
                   label="FORMAL", catalog_path=None, max_targets=None,
                   skip_perturbation=False, skip_baselines=False,
                   ledger_path=None) -> dict:
    """Full frozen evaluation. Discovery runs BLIND; labels are applied
    only to the produced outputs.

    FORMAL runs (label exactly "FORMAL") claim the holdout by content
    hash in the private single-use ledger BEFORE any label is parsed; a
    failure after claim permanently consumes the holdout.
    """
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    verify_frozen(receipt)

    formal = label == FORMAL_LABEL
    holdout_sha = None
    if formal:
        # Claim by exact file bytes, never contents, before label access.
        holdout_sha = _sha256_file(targets_path)
        run_id = f"formal_{time.strftime('%Y%m%dT%H%M%S')}_" \
                 f"{holdout_sha[:8]}"
        claim_formal_holdout(
            ledger_path or SINGLE_USE_POLICY["ledger_path"],
            holdout_sha256=holdout_sha,
            freeze_receipt_sha256=_sha256_obj(receipt),
            evaluator_sha256=receipt["evaluator_file_sha256"],
            production_generation=receipt["production_commit"],
            run_id=run_id, artifact_path=str(artifact_dir))
    else:
        run_id = f"run_{label}_{time.strftime('%Y%m%dT%H%M%S')}"

    targets = load_targets(targets_path)   # labels enter here, post-claim
    if max_targets:
        targets = targets[:max_targets]

    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    run = {"label": label, "evaluator_version": EVALUATOR_VERSION,
           "receipt": str(receipt_path),
           "targets_file": str(targets_path), "started_at":
               time.strftime("%Y-%m-%dT%H:%M:%S"),
           "production_commit": receipt["production_commit"]}

    try:
        aggregate = _run_evaluation_body(
            receipt, targets, artifact_dir, run, label, catalog_path,
            skip_perturbation)
    except Exception:
        if formal:
            # The labels were exposed: the holdout is consumed forever.
            mark_claim_status(ledger_path or SINGLE_USE_POLICY["ledger_path"],
                              holdout_sha, "FAILED_AFTER_CONSUMPTION",
                              str(artifact_dir))
        raise
    if formal:
        mark_claim_status(ledger_path or SINGLE_USE_POLICY["ledger_path"],
                          holdout_sha, "COMPLETED", str(artifact_dir))
    return aggregate


def _run_evaluation_body(receipt, targets, artifact_dir, run, label,
                         catalog_path, skip_perturbation) -> dict:

    # Scorability first (post-hoc evidence lookup, per frozen policy).
    scorability = []
    for t in targets:
        t_date = first_qualifying_evidence(t, catalog_path)
        scorability.append({
            "target_id": t["target_id"],
            "verdict": "SCORABLE" if t_date else
                       SCORABILITY_POLICY["missing_evidence_verdict"],
            "T": t_date,
        })
    _write(artifact_dir / "target-scorability.json", scorability)

    scorable = [t for t, s in zip(targets, scorability) if s["T"]]
    checkpoint_results, matched_negative_results = [], []

    import tempfile
    with tempfile.TemporaryDirectory(
            prefix="cd-eval-", ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        for t in scorable:
            registry = tmp / f"reg-{t['target_id']}.sqlite"
            cps = checkpoint_dates(_find_t(scorability, t["target_id"]))
            states = []
            for cp_label, cp_date in cps:
                summary = replay_as_of(registry, cp_date, catalog_path)
                concepts = _concept_names(registry)
                matched = [c for c in concepts if match_concept(c, t)]
                others = sorted(
                    (c["world_signal_score"] or 0 for c in concepts
                     if c["concept_id"] not in {m["concept_id"]
                                                for m in matched}),
                    reverse=True)
                states.append({
                    "checkpoint": cp_label, "as_of": cp_date,
                    "matched": [
                        {"concept_id": m["concept_id"],
                         "lifecycle": m["lifecycle_state"],
                         "world_signal": m["world_signal_score"],
                         "percentile": _percentile(
                             matched[0]["world_signal_score"] or 0, others)
                         if matched else None} for m in matched],
                    "candidates_total": summary.get("candidates", 0),
                    "emerging_total": summary.get("emerging", 0),
                })
            checkpoint_results.append({"target_id": t["target_id"],
                                       "checkpoints": states})

        # Negative controls at T-30 per frozen policy + baseline features.
        baseline_rows = []
        for t in scorable:
            registry = tmp / f"negctl-{t['target_id']}.sqlite"
            t_date = _find_t(scorability, t["target_id"])
            replay_as_of(registry, _shift(t_date, -30), catalog_path)
            rows = _concept_names(registry)
            matched_rows = [r for r in rows if match_concept(r, t)]
            matched_ids = {r["concept_id"] for r in matched_rows}
            anchor = (matched_rows or rows or [{}])[0]
            controls = select_negative_controls(anchor, rows, matched_ids)
            replay_as_of(registry, _shift(t_date, 30), catalog_path)
            final = _concept_names(registry)
            final_by_id = {r["concept_id"]: r for r in final}
            for cid in controls:
                row = final_by_id.get(cid)
                matched_negative_results.append({
                    "target_id": t["target_id"], "control_id": cid,
                    "emerging_at_T30":
                        bool(row and row["lifecycle_state"] == "emerging"),
                })
                if row:
                    f = extract_features(registry, cid)
                    baseline_rows.append(
                        {"kind": "control", **_baseline_flags(f)})
            for m in matched_rows:
                f = extract_features(registry, m["concept_id"])
                baseline_rows.append(
                    {"kind": "target", **_baseline_flags(f)})
        baseline_comparison = _compare_baselines(baseline_rows)
        _write(artifact_dir / "baseline-comparison.json", baseline_comparison)

        # Perturbation (temp snapshot; production never touched).
        perturbation_results = []
        if not skip_perturbation:
            for t in scorable:
                t_date = _find_t(scorability, t["target_id"])
                entry = {"target_id": t["target_id"]}
                for fraction in \
                        PERTURBATION_POLICY["removal_fractions"]:
                    snap = snapshot_catalog(
                        tmp / f"snap-{t['target_id']}-{fraction}.sqlite",
                        catalog_path)
                    removed = perturb_target_observations(snap, t, fraction)
                    registry = tmp / f"preg-{t['target_id']}-{fraction}.sqlite"
                    replay_as_of(registry, _shift(t_date, 30), snap)
                    concepts = _concept_names(registry)
                    matched = [c for c in concepts if match_concept(c, t)]
                    entry[f"removed_{int(fraction * 100)}"] = removed
                    entry[f"retained_{int(fraction * 100)}"] = bool(matched)
                    entry[f"emerging_{int(fraction * 100)}"] = bool(
                        matched and matched[0]["lifecycle_state"]
                        == "emerging")
                perturbation_results.append(entry)
        _write(artifact_dir / "perturbation-results.json",
               perturbation_results)

    _write(artifact_dir / "checkpoint-results.json", checkpoint_results)
    _write(artifact_dir / "negative-controls.json",
           matched_negative_results)

    # Baselines + aggregate + verdict.
    aggregate = aggregate_metrics(checkpoint_results,
                                  matched_negative_results,
                                  perturbation_results, len(scorable))
    aggregate["verdict"] = apply_verdict_v2(aggregate, baseline_comparison)
    _write(artifact_dir / "aggregate-summary.json", aggregate)
    _write(artifact_dir / "evaluation-report.md", _report_md(
        label, aggregate, baseline_comparison, len(scorable),
        len(targets) - len(scorable)))
    _write(artifact_dir / "evaluation-plan.json", {
        "label": label, "receipt": receipt,
        "metric_plan": METRIC_PLAN, "matching": MATCHING_POLICY,
        "scorability": SCORABILITY_POLICY,
        "negative_controls": NEGATIVE_CONTROL_POLICY,
        "perturbation": PERTURBATION_POLICY, "baselines": BASELINE_POLICIES,
        "verdict_rules": VERDICT_RULES, "single_use": SINGLE_USE_POLICY,
        "uncertainty": UNCERTAINTY_POLICY})
    run["aggregate"] = aggregate
    run["verdict"] = aggregate["verdict"]
    _write(artifact_dir / "run.json", run)
    return aggregate


def _report_md(label, aggregate, baselines, scorable, unscorable) -> str:
    lines = [
        f"# Concept-discovery retrospective evaluation — {label}",
        "",
        f"- evaluator: {EVALUATOR_VERSION} (frozen)",
        f"- scorable targets: {scorable}; unscorable (missing evidence): "
        f"{unscorable}",
        f"- candidate recall (scorable): "
        f"{aggregate.get('candidate_recall_scorable')}",
        f"- emerging recall (scorable): "
        f"{aggregate.get('emerging_recall_scorable')}",
        f"- matched-negative emerging rate: "
        f"{aggregate.get('matched_negative_emerging_rate')}",
        f"- perturbation retention 10%/20%: "
        f"{aggregate.get('perturbation10_retention')} / "
        f"{aggregate.get('perturbation20_retention')}",
        f"- policy vs baselines separation: "
        f"{baselines.get('policy_separation')} "
        f"(A {baselines.get('baseline_A_separation')}, "
        f"B {baselines.get('baseline_B_separation')})",
        f"- matched negative controls: "
        f"{aggregate.get('matched_negative_controls')} "
        f"(avg/target {aggregate.get('negatives_per_target_avg')})",
        f"- Wilson 95% intervals: {json.dumps(aggregate.get('wilson_95'))}",
        f"- VERDICT: {aggregate.get('verdict')}",
        "",
        "Aggregate metrics only. Target-level detail stays outside the "
        "public repository per the contamination protocol.",
    ]
    if aggregate.get("verdict") == "INSUFFICIENT_EVIDENCE":
        lines.append("")
        lines.append(
            "INSUFFICIENT_EVIDENCE: the scorable sample and/or matched-"
            "negative coverage is below the preregistered minimums "
            "(20 scorable targets; 40 controls; 2.0 controls/target). "
            "No PASS/PARTIAL/FAIL interpretation is emitted; a NEW unseen "
            "holdout generation is required.")
    if label != "FORMAL":
        lines.append("")
        lines.append(
            f"**LABEL: {label} — NON-BLIND / NOT PROMOTION EVIDENCE.**")
    return "\n".join(lines) + "\n"


def _find_t(scorability, target_id):
    return next(s["T"] for s in scorability
                if s["target_id"] == target_id)


def _shift(date_str: str, days: int) -> str:
    import datetime
    d = datetime.date.fromisoformat(date_str) + datetime.timedelta(days=days)
    return d.isoformat()


def _percentile(score: float, sorted_desc: list) -> float:
    if not sorted_desc:
        return 1.0
    lower = sum(1 for s in sorted_desc if s < score)
    return round(lower / len(sorted_desc), 3)


def aggregate_metrics(checkpoint_results, negatives, perturbations,
                      scorable_count: int) -> dict:
    """Metrics per the FROZEN METRIC_PLAN. No target-specific detail."""
    def reached_lifecycle(result, want: str) -> str | None:
        for cp in result["checkpoints"]:
            for m in cp["matched"]:
                if m["lifecycle"] == want:
                    return cp["checkpoint"]
        return None

    def reached_any(result) -> str | None:
        for cp in result["checkpoints"]:
            if cp["matched"]:
                return cp["checkpoint"]
        return None

    cand = [r for r in checkpoint_results if reached_any(r)]
    emg = [r for r in checkpoint_results if reached_lifecycle(r, "emerging")]

    def median_days(results, reach) -> float | None:
        days = []
        for r in results:
            cp = reach(r)
            if cp:
                days.append(next(o for o, l in CHECKPOINT_OFFSETS
                                 if l == cp))
        if not days:
            return None
        days.sort()
        return days[len(days) // 2]

    neg_rate = (sum(1 for n in negatives if n["emerging_at_T30"]) /
                len(negatives)) if negatives else None
    n_cand = len([r for r in checkpoint_results if reached_any(r)])
    n_emg = len([r for r in checkpoint_results
                 if reached_lifecycle(r, "emerging")])
    n_p10 = sum(1 for p in perturbations if p.get("retained_10"))
    n_p20 = sum(1 for p in perturbations if p.get("retained_20"))
    n_neg = len(negatives)
    aggregate = {
        "scorable_targets": scorable_count,
        "matched_negative_controls": n_neg,
        "negatives_per_target_avg": round(n_neg / scorable_count, 3)
        if scorable_count else None,
        "candidate_recall_scorable": round(len(cand) / scorable_count, 3)
        if scorable_count else None,
        "emerging_recall_scorable": round(len(emg) / scorable_count, 3)
        if scorable_count else None,
        "median_time_to_candidate_days": median_days(checkpoint_results,
                                                     reached_any),
        "median_time_to_emerging_days": median_days(checkpoint_results,
                                                    lambda r:
                                                    reached_lifecycle(
                                                        r, "emerging")),
        "matched_negative_emerging_rate": round(neg_rate, 3)
        if neg_rate is not None else None,
        "perturbation10_retention": round(
            n_p10 / len(perturbations), 3) if perturbations else None,
        "perturbation20_retention": round(
            n_p20 / len(perturbations), 3) if perturbations else None,
        "candidates_per_checkpoint":
            _per_checkpoint_totals(checkpoint_results),
    }
    # 95% Wilson intervals for proportion metrics (uncertainty reporting;
    # they do not replace the frozen point-estimate thresholds).
    aggregate["wilson_95"] = {
        "candidate_recall_scorable": wilson_interval(
            n_cand, scorable_count) if scorable_count else None,
        "emerging_recall_scorable": wilson_interval(
            n_emg, scorable_count) if scorable_count else None,
        "matched_negative_emerging_rate": wilson_interval(
            sum(1 for n in negatives if n["emerging_at_T30"]), n_neg)
        if negatives else None,
        "perturbation10_retention": wilson_interval(
            n_p10, len(perturbations)) if perturbations else None,
        "perturbation20_retention": wilson_interval(
            n_p20, len(perturbations)) if perturbations else None,
    }
    return aggregate


def _per_checkpoint_totals(checkpoint_results):
    out = {}
    for r in checkpoint_results:
        for cp in r["checkpoints"]:
            out.setdefault(cp["checkpoint"], []).append(
                cp["candidates_total"])
    return [{"checkpoint": k, "mean_candidates": round(
        sum(v) / len(v), 1)} for k, v in sorted(out.items())]


def apply_verdict_v2(aggregate: dict,
                     baseline_comparison: dict | None) -> str:
    """Verdict-v2: sample sufficiency gates substantive interpretation.

    INSUFFICIENT_EVIDENCE (and no PASS/PARTIAL/FAIL evaluation) when:
    scorable targets < 20, OR matched negative controls < 40, OR
    controls/target < 2.0. Otherwise the frozen substantive verdict-v1
    thresholds decide PASS/PARTIAL/FAIL unchanged."""
    gate = VERDICT_RULES["INSUFFICIENT_EVIDENCE"]
    scorable = aggregate.get("scorable_targets") or 0
    negatives = aggregate.get("matched_negative_controls") or 0
    per_target = aggregate.get("negatives_per_target_avg")
    if (scorable < gate["min_scorable_targets"]
            or negatives < gate["min_matched_negative_controls"]
            or (per_target is not None
                and per_target < gate["min_negatives_per_target"])):
        return "INSUFFICIENT_EVIDENCE"
    return apply_verdict(aggregate, baseline_comparison)


def apply_verdict(aggregate: dict, baseline_comparison: dict | None) -> str:
    rules = VERDICT_RULES["PASS"]
    cr = aggregate.get("candidate_recall_scorable") or 0
    er = aggregate.get("emerging_recall_scorable") or 0
    nr = aggregate.get("matched_negative_emerging_rate") or 0
    p20 = aggregate.get("perturbation20_retention") or 0
    beats = (baseline_comparison or {}).get("policy_beats_baselines")
    fail = VERDICT_RULES["FAIL"]
    if beats is False or \
            nr >= fail["or_matched_negative_emerging_rate_min"] or \
            (aggregate.get("perturbation20_retention") is not None
             and p20 <= fail["or_perturbation20_retention_max"]):
        return "FAIL"
    if (cr >= rules["candidate_recall_scorable_min"]
            and er >= rules["emerging_recall_scorable_min"]
            and nr <= rules["matched_negative_emerging_rate_max"]
            and p20 >= rules["perturbation20_retention_min"]
            and beats is True):
        return "PASS"
    return "PARTIAL"


def calibrate(artifact_dir, catalog_path=None) -> dict:
    """B10 calibration diagnostic — target-free reproduction of a current
    read-only scan against a temp registry."""
    import tempfile
    from ef import concept_discovery, concept_registry
    with tempfile.TemporaryDirectory(prefix="cd-calib-") as tmp:
        reg = Path(tmp) / "calibration-registry.sqlite"
        conn = concept_registry.connect(str(reg))
        try:
            summary = concept_discovery.scan_internal(
                conn, catalog_path=str(catalog_path or CATALOG))
        finally:
            conn.close()
        rows = _concept_names(reg)
        scores = sorted((r["world_signal_score"] or 0) for r in rows)
        emerging = [r for r in rows if r["lifecycle_state"] == "emerging"]
        out = {
            "entities_scanned": summary.get("entities_scanned"),
            "candidates": summary.get("candidates"),
            "emerging": summary.get("emerging"),
            "emerging_fraction": round(
                len(emerging) / len(rows), 3) if rows else None,
            "world_signal_distribution": {
                "p10": _q(scores, 0.1), "median": _q(scores, 0.5),
                "p90": _q(scores, 0.9)},
            "source_diversity_distribution": {
                "median": sorted(
                    r["source_diversity"] or 0 for r in rows)[
                    len(rows) // 2] if rows else None},
            "note": "reproduction of prior implementer smoke; "
                    "calibration evidence only",
        }
        _write(Path(artifact_dir) / "calibration-diagnostic.json", out)
        return out


def _q(sorted_vals, q) -> float:
    if not sorted_vals:
        return None
    return sorted_vals[int(q * (len(sorted_vals) - 1))]


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifact-root", default=str(EVAL_ARTIFACT_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("freeze")
    p.add_argument("--receipt-dir", default=None)
    p.add_argument("--production-sha", default=None)
    p.set_defaults(fn=lambda a: _cmd_freeze(a))

    p = sub.add_parser("run")
    p.add_argument("--receipt", required=True)
    p.add_argument("--targets", required=True)
    p.add_argument("--label", default="FORMAL")
    p.add_argument("--catalog", default=None)
    p.add_argument("--ledger", default=None,
                   help="single-use holdout ledger override (tests)")
    p.add_argument("--max-targets", type=int, default=None)
    p.add_argument("--skip-perturbation", action="store_true")
    p.set_defaults(fn=lambda a: _cmd_run(a))

    p = sub.add_parser("calibrate")
    p.add_argument("--receipt", default=None)
    p.add_argument("--catalog", default=None)
    p.set_defaults(fn=lambda a: _cmd_calibrate(a))

    a = ap.parse_args(argv)
    return a.fn(a)


def _cmd_freeze(a) -> int:
    run_id = time.strftime("%Y%m%dT%H%M%S")
    d = Path(a.receipt_dir) if a.receipt_dir else \
        Path(a.artifact_root) / f"freeze-{run_id}"
    receipt = build_freeze_receipt(d, a.production_sha)
    print(json.dumps(receipt, indent=2))
    print(f"[frozen] {d / 'frozen-code-hashes.json'}")
    return 0


def _cmd_run(a) -> int:
    d = Path(a.artifact_root) / f"eval-{time.strftime('%Y%m%dT%H%M%S')}" \
        f"-{a.label}"
    aggregate = run_evaluation(a.receipt, a.targets, d, label=a.label,
                               catalog_path=a.catalog,
                               ledger_path=a.ledger,
                               max_targets=a.max_targets,
                               skip_perturbation=a.skip_perturbation)
    print(json.dumps(aggregate, indent=2))
    print(f"[artifacts] {d}")
    return 0


def _cmd_calibrate(a) -> int:
    d = Path(a.artifact_root) / f"calibrate-{time.strftime('%Y%m%dT%H%M%S')}"
    out = calibrate(d, a.catalog)
    print(json.dumps(out, indent=2))
    print(f"[artifacts] {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
