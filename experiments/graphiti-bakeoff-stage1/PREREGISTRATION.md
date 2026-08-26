# Stage-1 Preregistration: Graphiti+FalkorDB vs yt-is relational memory

Frozen BEFORE any arm implementation ran against the fixture. Do not alter after
comparison results. Fixture: `fixture.json` (hash below).

- date: 2026-08-26, session: sess_16885634-0d0a-47a6-a2ab-1a9072a020b9 (agent: zcode)
- Arm A: EXISTING yt-is relational architecture (ef/concept_registry.py, ef/personal_graph.py,
  SQLite WAL) + ONLY the minimum machinery the frozen cases require (isolated adapter
  under experiments/; production system untouched).
- Arm B: Graphiti graphiti-core (current release) + ordinary supported FalkorDB server
  (>= 1.1.2). FalkorDBLite forbidden. Arm B runs only when a FalkorDB endpoint exists.

## Fixture hash
sha256(fixture.json) recorded in RECEIPT.md at freeze time.

## Support model (frozen)
- A claim/relationship is SUPPORTED when asserted by EUs from >= 2 distinct sources.
- Emergence time = t of the EU from the second independent source.
- An ASSERTED (single-source) claim is stored with provenance but not SUPPORTED.
- Supersession: later EU replaces current value; prior value stays queryable as-of
  earlier times. Contradiction: different values, no supersession marker -> coexist.

## Expected answers (frozen)

| id | query | expected |
|----|-------|----------|
| X1 as-of 2026-01-15 | E2 launch_year | 2031, ASSERTED-ONLY (EU03, single source) |
| X2 as-of 2026-01-19 | E1 partners_with O1 | SUPPORTED, emerged 2026-01-18 (EU06 S2, EU08 S3) |
| X3 as-of 2026-01-25 | E2 launch_year current | 2033, SUPPORTED (EU03, EU09); 2031 historical as-of <2026-01-20 |
| X4 now | E1 budget | 2M (EU05) and 5M (EU07) COEXIST as competing claims, each single-source |
| X5 now | aliases "the Alphard program", "ALPHARD initiative", "Alphard" resolve to E1; "Alphard Minor" resolves to E3 (distinct) |
| X6 now | bridge between T1 and T2 | path T1<-B1->T2 via EU10+EU11 (independent sources), SUPPORTED at aggregate, emerged 2026-01-24; B1 was not a predeclared Interest |
| X7 remove EU08 | partners_with E1-O1 downgrades to ASSERTED-ONLY (EU06); E1 researches T1 still SUPPORTED (EU01+EU02); everything else unchanged |
| X8 remove EU11 | bridge T1-T2 disappears; B1 enables T1 (EU10) remains |
| X9 now | P1 leads E1 | SUPPORTED, emerged 2026-02-02 (EU12 S2, EU15 S3) |
| X10 as-of 2026-01-26T00:00:00Z (inclusive) | P1 leads E1 | ASSERTED-ONLY (EU12 only; EU15 is post-T and MUST NOT leak) |
| X11 replay checkpoints | 01-05: E1 researches T1 becomes SUPPORTED; 01-18: partners emerges; 01-20: launch 2031->2033; 02-02: P1 leads E1 becomes SUPPORTED |
| X12 provenance | E1 partners_with O1 -> exactly {EU06, EU08}; E1 researches T1 -> exactly {EU01, EU02} |
| X13 why-surfaced | X6 answer must include: discovery route (path), supporting EUs (EU10, EU11), novelty state, evidence maturity (source count/timestamps), bridge reason |
| X14 concurrency | A reads gen N; B adds EU16 (new evidence) commits; C as-of replay OK; A stale write against gen N must fail or be explicitly versioned — behavior recorded, not assumed |

X11 replay case list also: no post-T leakage at each checkpoint (checked by re-query).

## Metrics
- Semantics: per-case PASS/FAIL against the table above (14 cases).
- Engineering: custom semantic lines/modules outside the substrate (cloc per arm,
  separated: Arm A additions vs Arm B adaptation code).
- Performance: per-arm ingest latency (fixture load), per-query latency (mean of 3 runs),
  replay latency (4 checkpoints), ms precision, same machine.
- Operations: startup steps, persistence across restart, concurrency/stale-write behavior,
  backup/restore surface. Measured, NOT decision-dominant.

## Decision rule
RELATIONAL_SUPPORTED | GRAPHITI_SUPPORTED | NO_MATERIAL_DIFFERENCE | INSUFFICIENT_EVIDENCE
- RELATIONAL_SUPPORTED: Arm A passes >= 12/14 semantics AND needs no large bespoke
  temporal/graph layer (additions confined to the adapter, no production rewrite).
- GRAPHITI_SUPPORTED: Arm B materially beats Arm A on semantics (>= 2 net case wins,
  or equal cases with materially less custom code).
- INSUFFICIENT_EVIDENCE: Arm B could not run (no FalkorDB endpoint) or fixture invalid.
- Semantic correctness dominates operations. Graph-native-ness alone wins nothing;
  operational simplicity alone wins nothing.

## Failure classes
F-leak (post-T leakage), F-prov (provenance incomplete), F-collapse (contradiction lost),
F-supersede (history destroyed), F-identity (alias misresolution), F-bridge,
F-removal (cascade too much/little), F-replay (nondeterministic), F-conc (lost update).

## Environment constraint recorded at freeze
No FalkorDB server is runnable on this host at freeze time (no Docker, no WSL, no native
Windows build, no cloud credentials). Arm B execution therefore requires an operator
endpoint; if absent at decision time, decision = INSUFFICIENT_EVIDENCE by rule.
