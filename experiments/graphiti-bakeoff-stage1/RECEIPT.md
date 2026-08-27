# Stage-1 Receipt — Graphiti bakeoff (Intelligence Service memory)

Status: COMPLETE (Arm A executed; Arm B prepared, not executable)

Session: NEW IMPLEMENTER OPTIONAL cold-start completed (sess_16885634-0d0a-47a6-a2ab-1a9072a020b9, agent: zcode, host: zcode)

## Current scale (measured, read-only, from P:/.data/yt-is canonical stores)
- documents (transcripts cached): 290,731
- evidence units: 266,945 (895,597 chunks; Qdrant 895,597 points)
- concepts/entities: 7,390 surface forms / 388 clustered; concept-registry tables NOT instantiated in production
- relationships: 131,980 kg_edges (40,569 kg_nodes)
- sources/channels: 3,092 distinct channels in eu
- concurrency: single serialized query service (exclusive start-lock); ~19 concurrent agent sessions (proxy); steady ingest ~1.3–2k EU/day
- manifest: experiments/graphiti-bakeoff-stage1/WORKLOAD.md (embedded in Stage-0 reports; see git history of this commit for source values)

## Preregistration
- path: experiments/graphiti-bakeoff-stage1/PREREGISTRATION.md
- sha256: a7bc91eb7dee054f9c193f8bfffe0e5303f175e76be76853eea9e0d36ec4005f
- fixture sha256: 1b3a258d110a7294a7ba146a866aeb81f56e7969111fd158aa152c10499cc0ce
- frozen before any arm ran; reviewer verified hash + mtime ordering

## Arm A
- current relational generation: reuse of ef/concept_registry.py (identity, aliases, observations w/ evidence_ref, relations, ensure_schema); production untouched
- added semantic mechanism surface: 509 LOC (arm_a/store.py): assertion versioning (valid_from/to, supersedes), tombstone downgrade, live as-of views, support/emergence computation, bridge admission (adds_source rule), replay, generation-CAS guard
- results: 14/14 PASS (X1..X14), reproducible (reviewer rerun), surgical removal, real stale-write failure

## Arm B
- Graphiti: graphiti-core 0.29.3 (Apache-2.0, Python >=3.10,<4)
- FalkorDB: client falkordb 1.7.1; SERVER UNAVAILABLE on this host (no Docker, no WSL, no native Windows build, no cloud endpoint). Run attempt: exit 3 FALKORDB_UNAVAILABLE (ConnectionError 10061 :6379)
- dependency lock: arm_b/lock.txt (pip freeze of arm_b/.venv)
- LLM-free ingestion verified feasible (direct node/edge save bypasses add_episode LLM pipeline; no-op client stubs). Graphiti free semantics: bi-temporal edge fields (storage only), episodic provenance, group_id, episode DETACH deletion. NOT provided LLM-free: entity dedup, supersession logic, as-of filtering (embedding-dependent search), traversal beyond provided Cypher, transactions/CAS, support model.
- REVIEWER DEFECT (HIGH): adapter ingest crashes at EU03 (literal object "2031" not an entity uuid); adapter is unvalidated code — its 411 LOC (filtered) / 466 (raw) must not be cited as a working adaptation. Fix required before any executed comparison.

## Semantic correctness
- Arm A: 14/14 (as-of, provenance, contradiction, supersession, identity, bridge, evidence-removal, replay, concurrency all PASS)
- Arm B: NOT EXECUTED (no endpoint)

## Performance (Arm A only, fixture scale)
- ingest 1.55 ms (median of 3 fresh loads); queries 0.02–0.12 ms; replay 1.08–2.05 ms per checkpoint. Fixture-scale numbers only; NOT production-scale latency evidence.

## Decision
INSUFFICIENT_EVIDENCE
(Arm B could not run — frozen-rule branch; prereg pre-committed to it. Arm A's unopposed 14/14 cannot trigger RELATIONAL_SUPPORTED. Preliminary signal, NOT a decision: even LLM-free, Graphiti would carry only storage primitives for this fixture's semantics — support model, supersession logic, dedup, as-of filtering all remain adapter code in both arms.)

## Production backend changed: NO
## Stage 2 executed: NO

## Review
- receipt: APPROVE_WITH_NOTES, independent fresh-context reviewer (general-5-3-max), axes: fairness CONCERN (adapter-borne semantics in both arms), fixture CONCERN (unstated X6 bridge-admission rule, asymmetric between arms; EU09 marker encoding ambiguity), expected outputs OK (hand re-derived), as-of leakage OK, entity-resolution CONCERN (fixture too easy — external validity near zero), removal OK, code accounting CONCERN (mixed LOC bases; uniform: A=509 vs B=411 filtered), concurrency OK, decision-rule application OK.

## Stage-2 status
Not justified by Stage 1 (INSUFFICIENT_EVIDENCE). Precondition for any re-run: operator provides FalkorDB endpoint (runbook in session report); then fix Arm B ingest defect (literal-valued predicates as edge attributes, not entity nodes), align bridge-admission rule, uniform LOC basis, then execute both arms under the same frozen contract.
