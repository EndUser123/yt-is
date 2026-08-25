"""Candidate-plan model for full-coverage bounded bootstrap inference.

Policy stance: a dashboard top-N is allowed; an inference bootstrap top-N is
NOT. The legacy bootstrap sent only a global top-25 breadth-ranked subset of
clusters to the LLM, structurally dropping narrow interests. This module
replaces that truncation: every eligible cluster is planned exactly once
across bounded batches (<= max_per_call per batch). The legacy top-25
behavior is preserved as an explicit, reproducible baseline
(``BASELINE_POLICY``) for later comparison.

Priority affects order, never eligibility: every eligible cluster is covered
regardless of score. The priority formula is a mechanically justified
ordering, not a claimed optimum:
    score = 0.35*norm(channels) + 0.20*norm(len(sources))
            + 0.15*norm(active_months) + 0.30*norm(documents)
            + 0.10 bonus if phase == "emerging"
where norm(v, m) = v / m (m = per-set maximum floored at 1; 0.0 when m <= 0).

Pure logic: consumes plain inventory-entry dicts (see ``ef/evidence_clusters.py``
for the producer); no database, provider, network, or subprocess access.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

BOOTSTRAP_POLICY = "bootstrap-full-coverage-v1"
BASELINE_POLICY = "baseline-top25-breadth-v1"
DEFAULT_MAX_CLUSTERS_PER_CALL = 25


class PlanCoverageError(ValueError):
    """Raised when a plan violates its coverage contract."""


@dataclass(frozen=True)
class CandidateBatch:
    """One bounded call: a deterministic slice of eligible cluster ids."""

    batch_id: str
    cluster_ids: tuple[int, ...]


@dataclass(frozen=True)
class CandidatePlanMetrics:
    """Audit metrics for a plan; dropped_count is 0 for bootstrap policy."""

    eligible_count: int
    planned_count: int
    coverage_pct: float
    batch_count: int
    min_batch_size: int
    max_batch_size: int
    dropped_count: int
    exclusions: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CandidatePlan:
    """Deterministic candidate plan; plan_id excludes created_at."""

    plan_id: str
    policy: str
    created_at: str
    batch_size: int
    eligible_cluster_ids: tuple[int, ...]
    batches: tuple[CandidateBatch, ...]
    signatures: dict[int, str]
    metrics: CandidatePlanMetrics
    priority_scores: dict[int, float]

    def to_dict(self) -> dict:
        """Return a plain JSON-able dict (tuples become lists)."""
        return {
            "plan_id": self.plan_id,
            "policy": self.policy,
            "created_at": self.created_at,
            "batch_size": self.batch_size,
            "eligible_cluster_ids": list(self.eligible_cluster_ids),
            "batches": [
                {"batch_id": b.batch_id, "cluster_ids": list(b.cluster_ids)}
                for b in self.batches
            ],
            "signatures": {str(k): v for k, v in self.signatures.items()},
            "metrics": {
                "eligible_count": self.metrics.eligible_count,
                "planned_count": self.metrics.planned_count,
                "coverage_pct": self.metrics.coverage_pct,
                "batch_count": self.metrics.batch_count,
                "min_batch_size": self.metrics.min_batch_size,
                "max_batch_size": self.metrics.max_batch_size,
                "dropped_count": self.metrics.dropped_count,
                "exclusions": dict(self.metrics.exclusions),
            },
            "priority_scores": {str(k): v for k, v in self.priority_scores.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CandidatePlan":
        """Rebuild a CandidatePlan from ``to_dict`` output."""
        metrics = d["metrics"]
        return cls(
            plan_id=d["plan_id"],
            policy=d["policy"],
            created_at=d["created_at"],
            batch_size=d["batch_size"],
            eligible_cluster_ids=tuple(d["eligible_cluster_ids"]),
            batches=tuple(
                CandidateBatch(b["batch_id"], tuple(b["cluster_ids"]))
                for b in d["batches"]
            ),
            signatures={int(k): v for k, v in d["signatures"].items()},
            metrics=CandidatePlanMetrics(
                eligible_count=metrics["eligible_count"],
                planned_count=metrics["planned_count"],
                coverage_pct=metrics["coverage_pct"],
                batch_count=metrics["batch_count"],
                min_batch_size=metrics["min_batch_size"],
                max_batch_size=metrics["max_batch_size"],
                dropped_count=metrics["dropped_count"],
                exclusions=dict(metrics["exclusions"]),
            ),
            priority_scores={int(k): v for k, v in d["priority_scores"].items()},
        )


def priority_score(entry: dict, maxima: dict[str, float]) -> float:
    """Weighted breadth-first priority score (order only, never eligibility).

    maxima keys: "channels", "sources", "active_months", "documents".
    """

    def norm(value: float, m: float) -> float:
        return value / m if m > 0 else 0.0

    score = (
        0.35 * norm(entry["channels"], maxima["channels"])
        + 0.20 * norm(len(entry["sources"]), maxima["sources"])
        + 0.15 * norm(entry["active_months"], maxima["active_months"])
        + 0.30 * norm(entry["documents"], maxima["documents"])
    )
    if entry.get("phase") == "emerging":
        score += 0.10
    return round(score, 6)


def _compute_maxima(entries: list[dict]) -> dict[str, float]:
    """Per-set maxima floored at 1 so all-zero sets score 0."""

    def m(key: str) -> float:
        best = max((e[key] for e in entries), default=0)
        if key == "sources":
            best = max((len(e["sources"]) for e in entries), default=0)
        return float(max(best, 1))

    return {
        "channels": m("channels"),
        "sources": m("sources"),
        "active_months": m("active_months"),
        "documents": m("documents"),
    }


def _canonical_fingerprint(
    policy: str,
    batch_size: int,
    eligible_ids: list[int],
    signatures: dict[int, str],
    batches: list[CandidateBatch],
) -> str:
    payload = {
        "policy": policy,
        "batch_size": batch_size,
        "cluster_ids": eligible_ids,
        "signatures": {cid: signatures[cid] for cid in eligible_ids},
        "batches": [[b.batch_id, list(b.cluster_ids)] for b in batches],
    }
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return "plan_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _metrics(
    eligible_count: int,
    planned_count: int,
    batch_sizes: list[int],
    dropped_count: int,
    exclusions: dict,
) -> CandidatePlanMetrics:
    coverage = 100.0 if eligible_count == 0 else round(planned_count / eligible_count * 100, 2)
    return CandidatePlanMetrics(
        eligible_count=eligible_count,
        planned_count=planned_count,
        coverage_pct=coverage,
        batch_count=len(batch_sizes),
        min_batch_size=min(batch_sizes, default=0),
        max_batch_size=max(batch_sizes, default=0),
        dropped_count=dropped_count,
        exclusions=dict(exclusions or {}),
    )


def build_bootstrap_plan(
    entries: list[dict],
    max_per_call: int = DEFAULT_MAX_CLUSTERS_PER_CALL,
    now: str | None = None,
    exclusions: dict | None = None,
) -> CandidatePlan:
    """Plan every eligible cluster exactly once in bounded priority-ordered batches.

    Priority affects order, never eligibility. Identical input set yields an
    identical plan_id regardless of entry order or ``now``.
    """
    if max_per_call < 1:
        raise ValueError("max_per_call must be >= 1")
    created_at = now if now is not None else time.strftime("%Y-%m-%dT%H:%M:%S")
    maxima = _compute_maxima(entries)
    scores = {e["cluster_id"]: priority_score(e, maxima) for e in entries}
    ordered = sorted(entries, key=lambda e: (-scores[e["cluster_id"]], e["cluster_id"]))
    batches = tuple(
        CandidateBatch(f"b{i + 1:03d}", tuple(e["cluster_id"] for e in ordered[i:i + max_per_call]))
        for i in range(0, len(ordered), max_per_call)
    )
    eligible_ids = sorted(e["cluster_id"] for e in entries)
    signatures = {e["cluster_id"]: e["evidence_signature"] for e in entries}
    sizes = [len(b.cluster_ids) for b in batches]
    plan_id = _canonical_fingerprint(
        BOOTSTRAP_POLICY, max_per_call, eligible_ids, signatures, list(batches)
    )
    return CandidatePlan(
        plan_id=plan_id,
        policy=BOOTSTRAP_POLICY,
        created_at=created_at,
        batch_size=max_per_call,
        eligible_cluster_ids=tuple(eligible_ids),
        batches=batches,
        signatures=signatures,
        metrics=_metrics(len(entries), len(entries), sizes, 0, exclusions or {}),
        priority_scores=scores,
    )


def build_baseline_plan(
    entries: list[dict],
    baseline_size: int = 25,
    now: str | None = None,
    exclusions: dict | None = None,
) -> CandidatePlan:
    """Reproduce the legacy top-N breadth subset as an explicit truncation baseline.

    Ranking: (-channels, -documents, cluster_id). Only the top ``baseline_size``
    entries are planned; dropped_count records the documented truncation.
    """
    if baseline_size < 1:
        raise ValueError("baseline_size must be >= 1")
    created_at = now if now is not None else time.strftime("%Y-%m-%dT%H:%M:%S")
    maxima = _compute_maxima(entries)
    scores = {e["cluster_id"]: priority_score(e, maxima) for e in entries}
    ranked = sorted(entries, key=lambda e: (-e["channels"], -e["documents"], e["cluster_id"]))
    chosen = ranked[:baseline_size]
    batch = CandidateBatch("b001", tuple(e["cluster_id"] for e in chosen))
    eligible_ids = sorted(e["cluster_id"] for e in entries)
    signatures = {e["cluster_id"]: e["evidence_signature"] for e in entries}
    planned_ids = sorted(e["cluster_id"] for e in chosen)
    plan_id = _canonical_fingerprint(
        BASELINE_POLICY, baseline_size, planned_ids, signatures, [batch]
    )
    sizes = [len(batch.cluster_ids)] if chosen else []
    return CandidatePlan(
        plan_id=plan_id,
        policy=BASELINE_POLICY,
        created_at=created_at,
        batch_size=baseline_size,
        eligible_cluster_ids=tuple(eligible_ids),
        batches=(batch,) if chosen else (),
        signatures=signatures,
        metrics=_metrics(
            len(entries), len(chosen), sizes, len(entries) - len(chosen), exclusions or {}
        ),
        priority_scores=scores,
    )


def plan_coverage(plan: CandidatePlan) -> dict:
    """Report covered/eligible counts plus duplicate and missing cluster ids."""
    eligible = set(plan.eligible_cluster_ids)
    seen: list[int] = []
    for batch in plan.batches:
        seen.extend(batch.cluster_ids)
    duplicates = sorted({cid for cid in seen if seen.count(cid) > 1})
    covered_set = set(seen)
    missing = sorted(eligible - covered_set)
    covered = len(eligible & covered_set)
    pct = 100.0 if len(eligible) == 0 else round(covered / len(eligible) * 100, 2)
    return {
        "covered": covered,
        "eligible": len(eligible),
        "pct": pct,
        "duplicate_cluster_ids": duplicates,
        "missing_cluster_ids": missing,
    }


def validate_plan_coverage(
    plan: CandidatePlan, max_per_call: int = DEFAULT_MAX_CLUSTERS_PER_CALL
) -> None:
    """Validate the coverage contract; raise PlanCoverageError on violation.

    The full-coverage equality check is skipped for BASELINE_POLICY (it is the
    explicit truncation baseline); batch-size, duplicate, and batch-id checks
    still apply.
    """
    covered: list[int] = []
    for batch in plan.batches:
        covered.extend(batch.cluster_ids)
        if len(batch.cluster_ids) > max_per_call:
            raise PlanCoverageError(
                f"batch {batch.batch_id} has {len(batch.cluster_ids)} clusters, "
                f"exceeds max_per_call={max_per_call}"
            )
        if len(batch.cluster_ids) == 0:
            raise PlanCoverageError(f"batch {batch.batch_id} is empty")
    if len({b.batch_id for b in plan.batches}) != len(plan.batches):
        raise PlanCoverageError("batch ids are not unique")
    if len(set(covered)) != len(covered):
        dupes = sorted({cid for cid in covered if covered.count(cid) > 1})
        raise PlanCoverageError(f"clusters appear in multiple batches: {dupes}")
    if plan.policy == BASELINE_POLICY:
        return
    if set(covered) != set(plan.eligible_cluster_ids):
        missing = sorted(set(plan.eligible_cluster_ids) - set(covered))
        extra = sorted(set(covered) - set(plan.eligible_cluster_ids))
        raise PlanCoverageError(
            f"coverage mismatch: missing={missing} extra={extra}"
        )
