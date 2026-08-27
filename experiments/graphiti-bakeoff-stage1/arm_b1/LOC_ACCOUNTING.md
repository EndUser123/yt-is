# Arm B1 — LOC accounting, uniform rule

agent: zcode

## Rule (applied identically to all three arms)

Project-owned executable semantic LOC = non-blank, non-comment lines in files
whose content defines semantic behavior or pins semantic configuration.
Comment-only lines and blank lines excluded (lines beginning `#` after strip);
docstrings count as executable lines. HARNESS counted separately
(orchestration / file IO / reporting). Library and dependency LOC always
excluded. The same counter script reproduces B0's frozen "411 filtered" number
for `arm_b/adapter.py` exactly, which validates the counting method against the
earlier freeze.

## Three-number table

| Arm | Semantic LOC | Harness LOC | Semantic files | Harness files |
|---|---:|---:|---|---|
| A  (yt-is relational)          | **509**  | 446 | store.py (509)                                   | cases.py (263), run_stage1.py (182), __init__.py (1) |
| B0 (Graphiti direct-save DIAG) | **411**  | 175 | adapter.py (411)                                 | run_cases.py (175) |
| B1 (real Graphiti pipeline)    | **1130** | 458 | evaluate.py (818), b1_clients.py (217), ingest.py (95) | run_b1.py (244), selftest_mock.py (214, test harness) |

Post-test note: the evaluator grew from the first draft (762 -> 818 semantic
LOC) because the mock self-test exposed two real defects that were fixed in
evaluate.py, not papered over: as-of reads now filter assertions PER EU inside
long-lived edges (post-T corroboration must not leak backward; X10/X11), and
support for a superseded claim keys on subject+predicate evidential history
(X3: EU03 S1 + EU09 S2 make launch_year SUPPORTED even though the value
changed). Mock verification: all 13 graph-backed cases PASS on a faithful
mock of expected Graphiti output (`selftest_mock.py`); X14 is UNTESTABLE by
design without two real connections.

Recount commands reproducible; classification per arm stated mechanically so a
re-cut is possible without re-arguing:

- Arm A preserves its prior convention verbatim: store.py = semantic,
  everything else = harness. Caveat kept honest: `cases.py` contains case
  definitions; B1 keeps its whole X-battery INSIDE evaluate.py, so part of what
  A files under harness lives under semantic here. This asymmetry is inherited
  from the A-side classification, not introduced by B1.
- B0 unchanged from the earlier freeze (adapter 411 / run_cases 175).
- B1: `ingest.py` counts as semantic because it defines the ingestion contract
  that provenance joins depend on (episode name carries eu_id/source_id,
  sequential t-order, literal values only inside text). `b1_clients.py` pins
  provider/model/temperature/embedder/reranker/group-scheme — configuration as
  semantics by rule. Pure orchestration (purge, aggregation, results IO) sits in
  run_b1.py = harness.

## Reading

B1 costs ~2.2x Arm A's semantic LOC, but the budget buys different things:
A's 509 implements the memory substrate itself; B1's semantic LOC is almost
entirely EVALUATION SURFACE over a substrate whose own extraction, resolution,
invalidation and temporal bookkeeping are library-provided (graphiti-core +
LLM pipeline = zero project LOC there, excluded by rule). The comparison that
matters for the prereg decision rule ("equal cases with materially less custom
code") is therefore substrate-behavior LOC: for X1..X14 temporal/identity/
bridge behaviors, Graphiti contributes 0 custom lines and B1 adds evaluation;
Arm A's store.py IS the behavior under test.

X-case attribution is tabled in CONFIG.md / the final report: per frozen
prereg, custom semantic surface is evaluator logic only (support rule +
temporal reads + bridge Cypher + why-bundle assembly); nothing about entity
extraction, dedup, invalidation or as-of fields is hand-built in B1.
