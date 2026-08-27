# Stage-1 corrections — architect review round (2026-08-26)

agent: zcode. Packet: CONTINUE EXISTING IMPLEMENTER (correction of commit 7ea6142c).
Original frozen contract: PREREGISTRATION.md sha256 a7bc91eb… / fixture.json
sha256 1b3a258d… — re-verified unchanged this round (`sha256sum -c freeze-hashes.txt`).
This file documents authorized repairs made BEFORE the first valid B1 execution;
it does not alter the frozen contract.

## C1. Arm redefinition

- B0 = previous arm_b/ direct-save + no-op-clients adapter → reclassified
  GRAPHITI_STORAGE_ONLY_DIAGNOSTIC. Preserved unmodified as evidence.
- B1 = arm_b1/ — Graphiti through its real semantic pipeline: every EU enters via
  `Graphiti.add_episode(...)` with raw evidence text, reference_time = EU t,
  sequential in t order; no add_episode_bulk for temporal-critical correctness;
  no hand-built nodes/edges for ingestion.
- Stage-1 decision: only A vs B1 counts. B0 remains diagnostic.

## C2. LLM/extraction fairness strategy (pinned before execution)

- Chat LLM: local go-llm-proxy `http://127.0.0.1:8080/v1`, model
  `nemotron-3-5-lightning-free` (canonical wire form), small_model same,
  temperature 0, structured output json_schema — LIVE VERIFIED with a real key
  held in runtime env only; json_object fallback wired (`--llm-mode json_object`).
  Provider is free-tier; upstream variance is MEASURED by the 3-run design below,
  not assumed away.
- Embedder: local deterministic fastembed BAAI/bge-small-en-v1.5 (384 dim) — the
  proxy has no embeddings endpoint; deterministic embedder keeps the extraction
  layer as the only stochastic component.
- Reranker: graphiti's local BGERerankerClient (deterministic, live-probed).
- Repeatability: B1 runs ≥3 times from clean empty graphs; per-case agreement is
  reported as a Graphiti mechanism property; no majority-voting of correctness.
- Generative call count/token instrumentation permanently wired (CountingLLMClient).

## C3. Reviewer defects fixed

1. Literal-valued predicates: B1 never fabricates literal entity nodes; literals
   appear only inside episode text (Graphiti extracts edges naturally).
2. Bridge symmetry: the adds_source bridge-admission rule lives in the SHARED
   evaluator section of arm_b1/evaluate.py, so both arms answer the identical
   frozen X6 question; B0's divergent rule stays in the diagnostic only.
3. LOC accounting: one uniform rule now — project-owned executable semantic LOC
   (non-blank, non-comment lines defining semantic behavior or pinning semantic
   configuration); harness counted separately; dependency/library LOC excluded.
   Uniform table: A 509 semantic / 446 harness · B0 411 / 175 · B1 1130 / 458.

## C4. ENTITY_RESOLUTION_STRESS_DIAGNOSTIC (non-decision)

er_stress/: 9-case stress set (acronym↔full name, punctuation/case/suffix,
mid-timeline rename, shared short label across two entities, identical surface
name ×2 entities, multi-source alias accretion) + ground truth + rubric.
Arm A executed now: PASS 2 / PARTIAL 4 / FAIL 3 — exact-normalized resolution
misses everything semantic and silently collapses genuine ambiguity. B1 side has
a mechanical runbook (RUNBOOK_B1.md). Informs interpretation; cannot change the
Stage-1 verdict.

## C5. FalkorDB endpoint status

Stores searched at correction time: workspace filesystem (no falkordb config or
binaries), prior-session history (one irrelevant hit), repo state (client lib
only, no endpoint). Preferred route = disposable FalkorDB Cloud FREE instance
(no card) or any existing endpoint; neither exists yet on this host. Docker/WSL
install deliberately NOT used as default unblock (operator authorization
required).

Status after all corrections: READY_FOR_FALKORDB_ENDPOINT.

## Publication

- Original commit 7ea6142c verified path-limited to experiments/graphiti-bakeoff-stage1/
  (13 files, 2455 insertions); pushed to origin/agent/sess_16885634-0d0a-47a6-a2ab-1a9072a020b9/graphiti-bakeoff-stage1;
  remote SHA = 7ea6142c (new branch, fast-forward).
- Correction-round commit: see git log following this file's addition.
