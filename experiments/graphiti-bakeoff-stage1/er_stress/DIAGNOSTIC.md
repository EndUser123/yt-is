# DIAGNOSTIC — ENTITY_RESOLUTION_STRESS_DIAGNOSTIC

`label: NON_DECISION_DIAGNOSTIC`

Boundary statement first: this diagnostic **informs architectural
interpretation** of the Stage-1 bakeoff (which identity failures each substrate
suffers on harder-than-fixture aliases). It **CANNOT change the Stage-1
decision**: the decision remains bound to the frozen `../PREREGISTRATION.md`
X1..X14 rule set over `../fixture.json`. Nothing here reopens those cases or the
decision rule.

Scope guard: all work lives in `er_stress/`. The fixture is fully synthetic
(`ER-*` namespace). No frozen file was modified; X1..X14 machinery untouched.

## 1. What the stress set covers

| required stress pattern | case(s) | ground truth |
|---|---|---|
| acronym vs full name (HTSC / High-Temperature Superconductivity Initiative) | ERS01 | MERGE_TO_ONE |
| punctuation/case variants (helion-labs / Helion Labs, Inc. / HELION LABS, / Incorporated) | ERS02 | MERGE_TO_ONE |
| renamed organization mid-timeline (Vertex -> Vector Dynamics) | ERS03 | MERGE_TO_ONE |
| ambiguous short label used by TWO projects (Project K = Kestrel AND Kepler) | ERS04 (+control ERS05) | DISTINCT |
| identical surface name, two entities, disambiguation needed by context/source — same legal name AND type (Geminus Group x2 firms) | ERS06 | DISTINCT |
| identical surface across TYPES (org vs internal project, Lumen Grid Technologies) | ERS07 | DISTINCT |
| aliases for one entity arriving from different sources at different times (Sofia Rand / S. Rand / Sofia Q. Rand, PhD) | ERS08 | MERGE_TO_ONE |
| negative control: near-identical names that must NOT merge (Helion vs Helios Labs) | ERS09 | DISTINCT |

Each case in `fixture_er_stress.json` declares its expected relation
(`MERGE_TO_ONE`, `DISTINCT`) with explicit target ids.

## 2. Scoring rubric (v1, implemented identically in run_arm_a.py)

Per probe verdicts: `HIT_EXPECTED` / `MISS_UNRESOLVED` (None) /
`WRONG_ENTITY`.

- `MERGE_TO_ONE`: FAIL if any WRONG_ENTITY or zero hits; PASS if all hits;
  else PARTIAL (mix).
- `DISTINCT` (positional targets): FAIL if any collision (>=2 probes resolve to
  one id) or any WRONG_ENTITY; PASS if every probe hits its own target and all
  resolved ids pairwise differ; else PARTIAL.
- Case score weight: PASS=1, PARTIAL=0.5, FAIL=0. Reported as counts +
  weighted score (of 1.0).

The Arm B1 runbook applies the SAME rules to Graphiti's post-resolution node
clusters, so results are comparable arms-side-by-side.

## 3. Mechanism measured

Arm A resolves through its existing path ONLY:
`ef.concept_registry.resolve_alias` = equality lookup on `normalize_alias()`
(casefold, inter-word hyphen/underscore -> space, whitespace collapse, strip
leading/trailing punctuation), then `ORDER BY concept_id LIMIT 1`. No fuzzy
match, no context/source parameter, no temporal alias validity, no acronym
expansion. Deliberately NOT upgraded: the diagnostic measures the current
mechanism.

## 4. Arm A observed results (run 2026-08-26, Python 3.14.0,
results in `results_arm_a.json`)

**Scorecard: 9 cases - PASS 2, PARTIAL 4, FAIL 3 (weighted 0.444 / 1.0)**

| case | verdict | resolved vs expected |
|---|---|---|
| ERS01 acronym/full name | PARTIAL | `HTSC`->ER-T1 ok; expansion form -> None (never registered; char-level normalization cannot relate initiatve-expanded string to topic) |
| ERS02 punct/case variants | PARTIAL | 3/4 hit (`HELION LABS,`, `helion-labs`, legal form); `Helion Labs Incorporated` -> None (suffix spelled out is a different string) |
| ERS03 renamed org | PARTIAL | pre-rename `Vertex Dynamics` -> None (flat alias table has no temporal validity); post-rename hits |
| ERS04 shared short label | FAIL | BOTH `Project K` mentions -> ER-PJ1. Alias table legally holds the normalized label under BOTH project concepts (`UNIQUE(normalized_alias, concept_id)` permits dual rows); resolver silently picks lexicographically-lowest concept id |
| ERS05 expansions control | PASS | Kestrel/Kepler cleanly separated |
| ERS06 identical name+type, two real firms | FAIL | both refs -> ER-G1. Worse than a resolution bug: `concept_identity_id = hash(type, name)` makes the two firms ONE concept id before any resolution happens; representation cannot encode them as two |
| ERS07 identical surface cross-type | FAIL | both refs -> ER-L1. Two type-scoped concepts DO exist, but they share the normalized alias and the resolver picks one, discarding the type information the caller did supply via context hints |
| ERS08 alias accretion over time/sources | PARTIAL | declared short form hits; `S. Rand`, `Sofia Q. Rand, PhD` -> None (initials/intra-name commas stay unregistered) |
| ERS09 near-name control | PASS | no false merge between Helion/Helios Labs |

## 5. What exact-normalized resolution misses most

One line: **it misses everything not reducible to character normalization -
acronym/expansion links, corporate-suffix spell-outs, renamed/historical names,
initial-and-credential forms, and ANY surface ambiguity (silent arbitrary pick)
including collisions it could refuse using the type dimension it already
stores.**

Falsifier framing for B1 comparison: if Graphiti's add_episode resolution
cluster shows HTSC-with-expansion, Vertex-with-Vector, Rand-initial-forms in
one node while keeping Kestrel/Kepler, Geminus-x2, Lumen org-vs-project as
separate nodes, its semantic resolution layer covers axes the relational
substrate leaves to callers. If it instead splits merged-surface cases worse or
over-merges Helion/Helios-class names, the trade records as measured here.

Verified-fact basis: table above comes from `results_arm_a.json` receipts
(normalized strings, candidate concept-id rows, resolved ids recorded per
probe). Interpretive claims are inference from those receipts plus the
implementation read of `ef/concept_registry.py` (normalize_alias, resolve_alias,
concept_identity_id) and `arm_a/store.py` (resolve_name).
