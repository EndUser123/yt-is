"""SHADOW open-world discovery planner (planning layer only).

Finds "things the operator may need to care about even though their
names are not already known" WITHOUT touching production: no canonical
Interest writes, no Recommendation changes, no burst-policy changes, no
new crawler. Discovery records stay in shadow output files.

Relation to ef.horizon_scout: this module REUSES the scout's execution
seam (the search_web MCP thin client pattern, spend-gated free tiers)
and its exploration-budget invariant (exploration > 0), but adds new
PLANNING operators on top:

  A. semantic step outward  (adjacency classes per anchor)
  B. cross-domain bridges   (anchor pairs across distinct domains)
  C. capability abstraction (hosts x capabilities x relations x standards)
  D. artifact fingerprints  (structural repo signals as queries/matchers)
  E. portability/converters (author-once, project-to-many-hosts)
  F. convergence detection  (same mechanism, independent ecosystems)

Planning is a pure function of input state: zero network calls. The
execution helper reuses the existing search fleet only.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable

SHADOW_POLICY_VERSION = "shadow-discovery-v1"
EXPLORATION_BUDGET = {"known_domain": 0.7, "adjacent": 0.2, "wildcard": 0.1}
_MAX_QUERIES = 48  # hard cap per shadow plan

# ---------------------------------------------------------------------------
# Part A — semantic step outward
# ---------------------------------------------------------------------------

# Each class names a RELATIONSHIP DIRECTION, never a specific product.
ADJACENCY_CLASSES = {
    "prerequisites": "prerequisites and foundations for {anchor}",
    "enabling_infrastructure": "infrastructure that enables {anchor}",
    "downstream_effects": "downstream effects and consequences of {anchor}",
    "neighboring_disciplines": "neighboring disciplines adjacent to {anchor}",
    "analogous_domains": "engineering fields analogous to {anchor}",
    "economic_dependencies": "economic dependencies and cost structure of {anchor}",
    "regulatory_dependencies": "regulation affecting {anchor}",
    "measurement_dependencies": "how to measure progress in {anchor}",
}

# ---------------------------------------------------------------------------
# Part B — cross-domain bridges
# ---------------------------------------------------------------------------

# Bridge patterns applied to anchor PAIRS from different domains. The
# packet's examples are instances, not the generator: pairs come from
# the anchor pool itself.
BRIDGE_PATTERNS = (
    "{a} applied to {b}",
    "{a} combined with {b} techniques",
    "what {a} can learn from {b}",
    "{a} and {b} intersection",
)

# ---------------------------------------------------------------------------
# Part C — capability abstraction (agentic-software knowledge graph seeds)
# ---------------------------------------------------------------------------

AGENT_HOSTS = ("Codex", "Claude", "Grok", "Pi", "ZCode", "OpenCode")
AGENT_CAPABILITIES = (
    "skills", "hooks", "subagents", "model routing", "memory and context",
    "worktrees and sandboxing", "scheduling", "evals", "tracing and replay",
    "security", "research and search", "reasoning and planning",
    "operator UX", "CI and software factory",
)
AGENT_STANDARDS = (
    "SKILL.md", "AGENTS.md", "MCP", "ACP", "A2A",
    "plugin manifest", "hook manifest", "provider adapter format",
)
CAPABILITY_RELATIONS = (
    "implements", "exposes", "supports", "converts_to",
    "compatible_with", "donor_for", "substitutes_for",
)

# ---------------------------------------------------------------------------
# Part D — artifact fingerprints
# ---------------------------------------------------------------------------

ARTIFACT_FINGERPRINTS = (
    "SKILL.md", "AGENTS.md", "plugin.json", "marketplace.json",
    "hooks.json", ".mcp.json", "skills/", "agents/", "extensions/",
    "providers/", "adapters/", "worktree", "sandbox", "trajectory", "replay",
)

# Fingerprint matchers applied to RESULT records (not just queries).
_FINGERPRINT_RE = re.compile(
    r"\b(SKILL\.md|AGENTS\.md|plugin\.json|marketplace\.json|hooks\.json|"
    r"\.mcp\.json)\b|/(skills|agents|extensions|providers|adapters)/",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Part E — portability / converter signals
# ---------------------------------------------------------------------------

PORTABILITY_QUERIES = (
    "generate agent config for multiple AI coding hosts from one source",
    "convert skills between AI coding assistants",
    "cross-host agent capability converter",
    "portable agent capability manifest standard",
    "compatibility layer for AI coding agent skills and hooks",
    "agent skills authored once deployed to many hosts",
)

PORTABILITY_SIGNALS = (
    "multi-host", "cross-host", "portable", "converter", "converts",
    "adapter", "compatibility layer", "generate", "renderer", "project",
)

# ---------------------------------------------------------------------------
# Part F — convergence detection vocabulary
# ---------------------------------------------------------------------------

# Mechanism keywords grouped so that records hitting the same group from
# structurally independent sources count as convergence evidence.
CONVERGENCE_MECHANISMS = {
    "skill_portability": ("skill", "SKILL.md", "portable", "convert", "cross-host"),
    "hook_enforcement": ("hook", "pre-tool", "guard", "policy enforcement"),
    "model_routing": ("model routing", "router", "escalation", "fallback model"),
    "worktree_isolation": ("worktree", "sandbox", "isolation"),
    "agent_protocol_bridge": ("MCP", "ACP", "A2A", "protocol bridge"),
    "trajectory_replay": ("trajectory", "replay", "session log"),
}

# ---------------------------------------------------------------------------
# Disposition vocabulary
# ---------------------------------------------------------------------------

DISPOSITIONS = ("ADOPT", "ADAPT", "DONOR-EXTRACT", "TEST", "WATCH", "IGNORE")


@dataclass(frozen=True)
class ShadowQuery:
    query_id: str
    operator: str  # adjacency|bridge|capability|fingerprint|portability|known|wildcard
    query: str
    exploration: str  # known_domain|adjacent|wildcard
    meta: dict = field(default_factory=dict)
    policy_version: str = SHADOW_POLICY_VERSION


@dataclass(frozen=True)
class ShadowPlan:
    plan_id: str
    created_at: str
    policy_version: str
    queries: tuple[ShadowQuery, ...]
    anchors_used: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "policy_version": self.policy_version,
            "anchors_used": list(self.anchors_used),
            "queries": [asdict(q) for q in self.queries],
        }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(text: str, n: int = 10) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def _norm(text: str) -> str:
    return re.sub(r"[^\w ]+", "", re.sub(r"\s+", " ", text.casefold()).strip())


def _mk(operator: str, query: str, exploration: str, **meta: Any) -> ShadowQuery:
    return ShadowQuery(
        query_id="sq_" + _hash(f"{operator}|{query}"),
        operator=operator,
        query=query,
        exploration=exploration,
        meta=meta,
    )


def _dedupe(queries: Iterable[ShadowQuery]) -> list[ShadowQuery]:
    out: list[ShadowQuery] = []
    seen: set[str] = set()
    for q in queries:
        key = _norm(q.query)
        if key and key not in seen:
            seen.add(key)
            out.append(q)
    return out


# ---------------------------------------------------------------------------
# Anchor loading (read-only, degrade-soft)
# ---------------------------------------------------------------------------


def load_anchors_from_catalog(catalog_conn: Any) -> list[str]:
    """Read-only anchor pool: interests/goals/questions (authoritative,
    currently sparse) enriched by top kg_nodes entities and topic-cluster
    labels. Never writes; missing tables degrade to empty."""
    import sqlite3

    anchors: list[str] = []
    queries = (
        "SELECT name FROM interests WHERE temporal_state IN ('durable','active','current_problem')",
        "SELECT statement AS a FROM goals",
        "SELECT text AS a FROM questions",
        "SELECT label AS a FROM topic_clusters ORDER BY member_count DESC LIMIT 25",
        "SELECT label AS a FROM kg_nodes WHERE kind='entity' ORDER BY weight DESC LIMIT 50",
    )
    for sql in queries:
        try:
            rows = catalog_conn.execute(sql).fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            label = str(r[0]).strip()
            if 2 <= len(label) <= 60:
                anchors.append(label)
    seen: set[str] = set()
    out = []
    for a in anchors:
        k = _norm(a)
        if k and k not in seen:
            seen.add(k)
            out.append(a)
    return out


# ---------------------------------------------------------------------------
# Operator generators
# ---------------------------------------------------------------------------


def adjacency_queries(anchors: list[str], per_anchor: int = 2) -> list[ShadowQuery]:
    """Part A: step outward from concrete operational anchors through
    relationship classes. The anchor is a CONCRETE domain, never the
    personal meta-goal."""
    classes = list(ADJACENCY_CLASSES.items())
    out: list[ShadowQuery] = []
    for i, anchor in enumerate(anchors):
        # deterministic class rotation so each anchor covers new classes
        rotation = classes[i % len(classes):] + classes[: i % len(classes)]
        for cls, tpl in rotation[:per_anchor]:
            out.append(
                _mk("adjacency", tpl.format(anchor=anchor), "adjacent",
                    adjacency_class=cls, anchor=anchor)
            )
    return _dedupe(out)


def bridge_queries(anchors: list[str], limit: int = 8) -> list[ShadowQuery]:
    """Part B: bridge searches BETWEEN established anchors. Pairs are
    drawn from the anchor pool itself; patterns are generic."""
    out: list[ShadowQuery] = []
    if len(anchors) < 2:
        return out
    step = max(1, len(anchors) // 2)
    pairs = list(zip(anchors, anchors[step:] + anchors[:step]))
    for i, (a, b) in enumerate(pairs):
        if _norm(a) == _norm(b):
            continue
        tpl = BRIDGE_PATTERNS[i % len(BRIDGE_PATTERNS)]
        out.append(_mk("bridge", tpl.format(a=a, b=b), "adjacent", pair=[a, b]))
        if len(out) >= limit:
            break
    return _dedupe(out)


def capability_queries(limit: int = 8) -> list[ShadowQuery]:
    """Part C: hosts x capabilities x standards/relation queries.
    Sampled with a stride across the full grid so coverage spreads over
    hosts and capabilities instead of exhausting one host first."""
    grid = [(h, c) for h in AGENT_HOSTS for c in AGENT_CAPABILITIES]
    out: list[ShadowQuery] = []
    if limit <= len(grid):
        step = len(grid) / limit
        sample = [grid[int(i * step)] for i in range(limit)]
    else:
        sample = grid
    for host, cap in sample:
        out.append(_mk(
            "capability", f"{host} {cap} extensions and plugins",
            "known_domain", host=host, capability=cap,
        ))
    for std in AGENT_STANDARDS[:4]:
        out.append(_mk(
            "capability", f"{std} support across AI coding tools",
            "adjacent", standard=std,
        ))
    return _dedupe(out)[:limit]


def fingerprint_queries(limit: int = 6) -> list[ShadowQuery]:
    """Part D: structural repository discovery signals as queries."""
    out = [
        _mk("fingerprint", '"SKILL.md" "AGENTS.md" github repository', "wildcard"),
        _mk("fingerprint", 'github "plugin.json" agent marketplace', "wildcard"),
        _mk("fingerprint", 'github ".mcp.json" "hooks.json" agent tooling', "wildcard"),
        _mk("fingerprint", 'github agent "skills" "extensions" providers directory', "wildcard"),
        _mk("fingerprint", 'github repository agent worktree sandbox workflow', "wildcard"),
        _mk("fingerprint", 'github agent trajectory replay session logs tool', "wildcard"),
    ]
    return out[:limit]


def portability_queries(limit: int = 4) -> list[ShadowQuery]:
    """Part E: seek converters, generators, adapters, compatibility
    layers — author once, project into multiple hosts."""
    out = [_mk("portability", q, "adjacent") for q in PORTABILITY_QUERIES]
    return out[:limit]


# ---------------------------------------------------------------------------
# Plan assembly
# ---------------------------------------------------------------------------


def build_shadow_plan(
    anchors: list[str],
    *,
    now: str | None = None,
    max_queries: int = 24,
    budget: dict | None = None,
) -> ShadowPlan:
    """Build a shadow discovery plan. Same anti-bubble invariant as the
    scout: known/adjacent/wildcard split follows the INITIAL 70/20/10
    budget (not claimed optimal), exploration > 0 always."""
    budget = budget or EXPLORATION_BUDGET
    max_queries = min(max_queries, _MAX_QUERIES)
    n_known = max(1, round(max_queries * budget["known_domain"]))
    n_adjacent = max(1, round(max_queries * budget["adjacent"]))
    n_wild = max(1, round(max_queries * budget["wildcard"]))

    # Reserve exploration slots FIRST (anti-bubble invariant: wildcard and
    # adjacent survive truncation at every budget), then fill known.
    wild = fingerprint_queries(limit=n_wild)
    adjacent: list[ShadowQuery] = []
    adjacent.extend(adjacency_queries(anchors, per_anchor=2))
    adjacent.extend(bridge_queries(anchors, limit=max(2, n_adjacent // 2)))
    adjacent.extend(portability_queries(limit=min(2, n_adjacent)))
    adjacent = adjacent[:n_adjacent]

    known: list[ShadowQuery] = []
    known.extend(capability_queries(limit=n_known))
    if len(known) < n_known:
        known.extend(adjacency_queries(anchors, per_anchor=1)[: n_known - len(known)])

    # unfillable slots spill to the richest remaining pool, never dropped
    shortfall = (n_known - len(known)) + (n_adjacent - len(adjacent)) + (n_wild - len(wild))
    if shortfall > 0:
        spill = (adjacency_queries(anchors, per_anchor=4)
                 + portability_queries(limit=6))
        wild.extend(q for q in spill if q not in wild)

    queries = _dedupe(wild + adjacent + known)[:max_queries]
    plan_id = "shadow_" + _hash("\n".join(sorted(q.query_id for q in queries)), 12)
    return ShadowPlan(
        plan_id=plan_id,
        created_at=now if now is not None else _now_iso(),
        policy_version=SHADOW_POLICY_VERSION,
        queries=tuple(queries),
        anchors_used=tuple(anchors),
    )


# ---------------------------------------------------------------------------
# Execution (reuses the search fleet seam; free tiers only)
# ---------------------------------------------------------------------------


def run_shadow(
    plan: ShadowPlan,
    *,
    mcp_call,
    tier: str = "fast",
    num_results: int = 8,
) -> list[dict]:
    """Execute a shadow plan through an injected search seam (the SAME
    search_web MCP client pattern horizon_scout uses — no new service).
    The seam signature is seam(tool, arguments) -> list[dict]."""
    out: list[dict] = []
    for q in plan.queries:
        try:
            raw = mcp_call(
                "query",
                {"search_query": q.query, "num_results": num_results, "tier": tier},
            )
        except Exception as exc:  # fail-soft per query, never fabricate
            out.append({"query_id": q.query_id, "operator": q.operator,
                        "query": q.query, "error": str(exc)})
            continue
        for rec in raw:
            if not isinstance(rec, dict) or not rec.get("url"):
                continue
            out.append({
                "query_id": q.query_id,
                "operator": q.operator,
                "query": q.query,
                "exploration": q.exploration,
                "title": str(rec.get("title") or ""),
                "url": str(rec["url"]),
                "snippet": str(rec.get("snippet") or ""),
                "backends": rec.get("rrf_backends", rec.get("backend", "")),
            })
    return out


# ---------------------------------------------------------------------------
# Post-processing: fingerprints, portability, convergence, disposition
# ---------------------------------------------------------------------------


def match_fingerprints(record: dict) -> list[str]:
    """Artifact fingerprints found in a result record's text fields."""
    text = " ".join((record.get("title", ""), record.get("snippet", ""), record.get("url", "")))
    hits = [fp for fp in ARTIFACT_FINGERPRINTS
            if fp.strip("./").lower() in text.lower()]
    if _FINGERPRINT_RE.search(text) and not hits:
        hits.append(_FINGERPRINT_RE.search(text).group(0))
    return hits


def portability_score(record: dict) -> int:
    text = " ".join((record.get("title", ""), record.get("snippet", ""))).lower()
    return sum(1 for s in PORTABILITY_SIGNALS if s in text)


def detect_convergence(records: list[dict]) -> list[dict]:
    """Part F: group shadow records by mechanism vocabulary. A finding
    reports mechanism, independent sources (distinct domains/hosts),
    evidence URLs, and overlap with our architecture (agentic fleet
    tooling). Trend/confidence is left to longitudinal data — a single
    run reports first-observed only."""
    findings: dict[str, dict] = {}
    for r in records:
        if "url" not in r:
            continue
        text = " ".join((r.get("title", ""), r.get("snippet", ""))).lower()
        for mech, kws in CONVERGENCE_MECHANISMS.items():
            if sum(1 for k in kws if k.lower() in text) >= 2:
                f = findings.setdefault(mech, {"mechanism": mech, "evidence": [],
                                               "distinct_domains": set()})
                f["evidence"].append({"title": r.get("title", ""), "url": r["url"],
                                      "operator": r.get("operator", "")})
                domain = re.sub(r"^https?://([^/]+).*", r"\1", r["url"])
                f["distinct_domains"].add(domain)
    out = []
    for mech, f in sorted(findings.items()):
        if len(f["evidence"]) < 2:
            continue  # single hit is not convergence
        out.append({
            "mechanism": mech,
            "independent_implementations": len(f["distinct_domains"]),
            "host_diversity": sorted(f["distinct_domains"])[:8],
            "evidence": f["evidence"][:10],
            "first_observed": _now_iso(),
            "trend": "first-observation",
            "confidence": "low" if len(f["distinct_domains"]) < 3 else "medium",
            "overlap_with_our_architecture": mech in {
                "skill_portability", "hook_enforcement", "model_routing",
                "worktree_isolation", "trajectory_replay",
            },
            "implication": "watch for standardization; evaluate donor extraction",
        })
    return out


def classify_disposition(record: dict) -> str:
    """Rule-based disposition from record signals. Explicitly NOT
    popularity-based: no star counts, no auto-ADOPT from fame."""
    if "error" in record or not record.get("url"):
        return "IGNORE"
    text = " ".join((record.get("title", ""), record.get("snippet", ""))).lower()
    fps = match_fingerprints(record)
    port = portability_score(record)
    if port >= 2 and fps:
        return "TEST"  # portable + fingerprinted: discriminating test first
    if port >= 2:
        return "ADAPT"
    if len(fps) >= 2:
        return "DONOR-EXTRACT"  # structurally close to our artifacts
    if fps or any(k in text for k in ("convert", "multi-host", "portable")):
        return "WATCH"
    return "WATCH" if record.get("operator") in ("adjacency", "bridge") else "IGNORE"


# ---------------------------------------------------------------------------
# Evaluation contract (for later blinded assessment)
# ---------------------------------------------------------------------------

EVALUATION_CONTRACT = {
    "purpose": "Blinded usefulness assessment of shadow discovery (Plan B) "
               "vs baseline scout (Plan A). No superiority claim before "
               "this contract is executed by an assessor blind to which "
               "plan produced which item.",
    "unit": "one discovery record (url + title + snippet + originating query)",
    "axes": {
        "useful_discoveries_gained": "count judged actionable or knowledge-extending",
        "irrelevant_discoveries_added": "count judged noise",
        "evidence_quality": "1-5 mean of primary-source availability",
        "detection_lead_time": "days vs first appearance in existing corpus",
        "novelty": "fraction not present in wiki/registry/corpus",
        "transfer_value": "fraction applicable beyond the anchor domain",
        "regret_miss_reduction": "assessor judgment, 1-5",
        "investigate_save_action_likelihood": "fraction the assessor would save/investigate/act on",
    },
    "controls": {
        "same_budget": "Plan A and Plan B run with the same query count and tier",
        "blinding": "items shuffled, plan origin stripped before assessment",
        "sample_size": "minimum 50 records per plan for a reportable comparison",
    },
    "success_rule": "Plan B superior only if useful_gained increases AND "
                    "irrelevant fraction does not increase at 95% interval",
    "falsifier": "blinded assessment shows adjacency/bridge/fingerprint "
                 "operators produce no more useful discoveries per query "
                 "than wildcard-only planning",
}


def shadow_report(plan: ShadowPlan, records: list[dict]) -> dict:
    """Assemble the full shadow state record: plan, per-operator stats,
    dispositions, convergence findings, evaluation contract."""
    by_op: dict[str, int] = {}
    for q in plan.queries:
        by_op[q.operator] = by_op.get(q.operator, 0) + 1
    disp: dict[str, list[str]] = {}
    for r in records:
        d = classify_disposition(r)
        disp.setdefault(d, []).append(r.get("url", ""))
    return {
        "plan": plan.to_dict(),
        "query_counts_by_operator": by_op,
        "records": len([r for r in records if "url" in r]),
        "errors": len([r for r in records if "error" in r]),
        "dispositions": {k: len(v) for k, v in disp.items()},
        "disposition_examples": {k: v[:10] for k, v in disp.items()},
        "convergence": detect_convergence(records),
        "evaluation_contract": EVALUATION_CONTRACT,
        "provenance": {"agent": "zcode", "mode": "shadow", "layer": "yt-is/ef"},
        "generated_at": _now_iso(),
    }
