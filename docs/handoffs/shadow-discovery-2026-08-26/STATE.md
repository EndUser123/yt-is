# Shadow Discovery Planner — State Record (2026-08-26)

agent: zcode | mode: shadow (no production changes) | policy: shadow-discovery-v1

## Identity

New shadow planner: `ef/shadow_discovery.py` (+ `tests/test_shadow_discovery.py`,
11 unit tests, all passing). Planning layer only; execution reuses the existing
search fleet.

## Scout machinery reused

- `ef/horizon_scout.py` (HEAD 87dbd9a8): execution seam
  (`_default_mcp_call` → search_web MCP on 127.0.0.1:8323), spend gate,
  free-tier-only rule, fail-soft per query, exploration-budget invariant.
- NOTE: `ef/horizon_scout.py` is staged-deleted in the shared checkout by
  another session. This work did NOT restore or modify it; the diagnostic
  imported it from the HEAD git blob (`git show HEAD:ef/horizon_scout.py`)
  into `P:/tmp`. Deviation from "isolated worktree": new path-disjoint files
  were added to the package root instead (48 registered worktrees; shared
  dirty index made a new worktree the higher-risk option).

## New discovery operators

| Part | Operator | Mechanism |
|---|---|---|
| A | adjacency | 8 relationship classes (prerequisites, enabling infra, downstream, neighboring, analogous, economic, regulatory, measurement) rotated per anchor |
| B | bridge | generic bridge patterns applied to anchor PAIRS from the anchor pool |
| C | capability | hosts × capabilities grid, strided sampling; standards queries |
| D | fingerprint | structural artifact queries (SKILL.md, AGENTS.md, plugin.json, …) + record-level matchers |
| E | portability | converter/generator/adapter/compat-layer queries + signal scorer |
| F | convergence | mechanism vocabulary grouping; requires ≥2 evidence items and ≥2 distinct domains |

Anchors: interests/goals/questions tables are EMPTY in catalog.sqlite
(authoritative store sparse), so `load_anchors_from_catalog` enriches from
topic_clusters (25) + kg_nodes entities (50) read-only → 75 anchors.
The personal-agency meta-goal is never used as a query.

## Plan comparison (diagnostic, 2026-08-26)

- Plan A (horizon_scout, max_queries=12): degrades to 4 wildcard queries —
  the interests/goals/information_needs tables it reads are empty. 63 records.
- Plan B (shadow, same 12-query budget cap): 11 queries (capability 9,
  adjacency 2, fingerprint 1 — adjacent cap 2 consumed by adjacency before
  bridges/portability; at this small budget bridges did not surface). 168
  records.
- Same tier (fast, free), same num_results (8). Zero transport errors.
- NOT a usefulness claim: more records is explicitly not success. The
  blinded evaluation contract (`EVALUATION_CONTRACT` in the module, copied
  into diagnostic_report.json) gates any superiority claim.

## Disposition snapshot (rule-based, not popularity-based)

DONOR-EXTRACT 33, WATCH 70, IGNORE 63, TEST 2. One convergence finding:
skill_portability, 3 independent domains, confidence medium (first
observation; no trend claim).

## Prohibitions honored

Production acquisition unchanged. Canonical Interests unchanged (read-only
connections). Recommendation unchanged. burst-policy untouched. No new
crawler/search service. 70/20/10 treated as initial policy, not optimum.

## Unresolved falsifier

Blinded assessment (contract's sample ≥50 records/plan) has NOT been run.
Falsifier: if adjacency/bridge/fingerprint operators produce no more useful
discoveries per query than wildcard-only planning, the shadow planner is
not better than the scout baseline — and Plan A's wildcard-only degradation
suggests the cheapest fix may simply be populating the interests/goals
tables, not a new planner.

## Artifacts

- `ef/shadow_discovery.py` — planner + operators + evaluation contract
- `tests/test_shadow_discovery.py` — 11 passing tests
- `docs/handoffs/shadow-discovery-2026-08-26/diagnostic_report.json`
- `docs/handoffs/shadow-discovery-2026-08-26/plans.json` (plans A+B, anchors)
