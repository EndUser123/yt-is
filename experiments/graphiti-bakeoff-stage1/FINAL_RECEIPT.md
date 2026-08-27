# Stage-1 FINAL RECEIPT — correction round complete

Status: READY_FOR_FALKORDB_ENDPOINT

Session: CONTINUE EXISTING IMPLEMENTER (sess_16885634-0d0a-47a6-a2ab-1a9072a020b9, agent: zcode)

Original commit: 7ea6142c
Published experiment branch (remote):
    agent/sess_16885634-0d0a-47a6-a2ab-1a9072a020b9/graphiti-bakeoff-stage1
    remote tip = f8a2d79d (7ea6142c -> e405be3c corrections -> f8a2d79d delta fixes;
    lane history merged through reviewed run-3de36c940d44; integration broker ran)

Frozen contract unchanged: YES
    sha256 fixture.json   = 1b3a258d110a7294a7ba146a866aeb81f56e7969111fd158aa152c10499cc0ce
    sha256 PREREGISTRATION.md = a7bc91eb7dee054f9c193f8bfffe0e5303f175e76be76853eea9e0d36ec4005f
    verified `sha256sum -c freeze-hashes.txt` after every round.

Arm A:
    cases passed: 14/14 X1..X14 (unchanged from 7ea6142c, reproducible)
    semantic LOC: 509 (uniform rule; harness 446)

Arm B0:
    classification: GRAPHITI_STORAGE_ONLY_DIAGNOSTIC
    result: preserved byte-identical vs 7ea6142c; never executed; evidence only

Arm B1:
    Graphiti version: graphiti-core 0.29.3 (Apache-2.0)
    FalkorDB version: client falkordb 1.7.1; server absent on host
    ingestion API: Graphiti.add_episode per EU in t order, raw text,
        reference_time = EU t; no add_episode_bulk, no hand-built nodes/edges
    LLM provider/model/config: local go-llm-proxy OpenAI-compatible
        model nemotron-3-5-lightning-free (canonical wire form, live-verified),
        small_model same, temperature 0, structured_output json_schema
        (json_object fallback wired); embedder deterministic local fastembed
        BAAI/bge-small-en-v1.5 (384 dim); reranker local BGE (live-probed);
        API key runtime-env only, never committed
    valid runs: 0 of 3 (endpoint unreachable — preregistered blocker)
    results: results_run{N}.json record blocked state honestly (no fabricated cases);
        evaluator proven end-to-end by selftest_mock: 13/13 read-only PASS,
        X14 UNTESTABLE-by-design pending endpoint
    repeatability: measurable once endpoint exists (--run 1/2/3 + aggregate)

Entity-resolution stress diagnostic:
    ENTITY_RESOLUTION_STRESS_DIAGNOSTIC (NON_DECISION): er_stress/
    Arm A executed: PASS 2 / PARTIAL 4 / FAIL 3 (weighted 0.444) — exact-normalized
    resolution misses all semantic variants and silently collapses genuine ambiguity
    B1 side mechanical runbook on pinned stack committed (run_b1_er_stress.py)

Semantic mechanism ownership (as implemented today):
    relational/application: support rule + emergence, supersession-chain claim
        resolution, bridge admission (adds_source), as-of live views, alias
        normalization (A); evaluator equivalents in B1 are flagged per case
    Graphiti: extraction pipeline (nodes/edges/facts), entity dedup/resolution
        machinery, edge invalidation writes, bi-temporal fields, episode-edge
        provenance backlinks, remove_episode semantics, indices/constraints
    FalkorDB: storage + Cypher traversal surface (autocommit-per-query found;
        no transactions/CAS — recorded for X14)
    LLM: extraction quality + resolution decisions inside add_episode; nondeterminism
        to be MEASURED across 3 runs, reported as mechanism property

Performance/cost: blocked with the endpoint. Instrumentation live (CountingLLMClient:
per-prompt call counts + token usage into results_run{N}.json). Arm A fixture-scale
latencies from round 1 stand (ingest 1.55 ms; queries 0.02–0.12 ms).

Decision: INSUFFICIENT_EVIDENCE (standing; frozen rule not yet applicable —
only A-vs-B1 can decide after valid execution)

Production backend changed: NO
Stage 2: NOT EXECUTED

Review receipts:
    correction-round delta review: APPROVE_WITH_NOTES (general-5-3-max, fresh context)
    formal fleet review: APPROVED, run-3de36c940d44, reviewer fresh context
        (authority manual, flagged), candidate tree 7236a811…; minor findings
        F1/F2/F3 logged non-blocking
    post-fix verification: py_compile clean; selftest_mock 13/13 re-run green

If blocked only on DB endpoint: READY_FOR_FALKORDB_ENDPOINT
    Minimal operator action:
    1. Create a disposable FalkorDB Cloud FREE instance (https://cloud.falkordb.com,
       no credit card) OR start any FalkorDB >= 1.1.2 server reachable from this machine.
    2. Paste the endpoint (host/port/username/password) into the session. Nothing else
       is needed — ingest/evaluate/aggregate are wired; pinned venvs and LLM route are
       live-tested. Do NOT commit credentials.
