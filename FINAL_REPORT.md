# Muse Spark harness run — FINAL REPORT

Worktree: `.worktrees/muse-spark-20260825-202949` · Branch: `harness/muse-spark-20260825-202949`
Base: `dcc76cd8` (main tip at prepare) · Ran: 2026-08-25, 9 code iterations + this closeout

## Stop condition

Iteration limit (10) reached with the 10th consumed by the closeout scan
below. **Exhaustion is NOT proven**: the module-by-module scan covers the
ef/ core and the test infrastructure; csf production code and scripts/
remain unreviewed (listed under recommendations). The suite is fully
green at exit.

## Findings fixed (16, one line each)

1. `/health` claimed ready before the encoder canary passed — the exact
   2026-08-23 incident shape; now honest 503 + `is_running` liveness
   preserved (ef/warm_query_service.py, 5fc4caec)
2. `/query` `top_k` unvalidated `int()` killed the handler uncaught; now
   400 + clamp 1..100 (ef/warm_query_service.py, 5fc4caec)
3. 15 dht-ingest tests dead since 69d397fa's `DB`→`SDB/TDB` rename;
   fixture + API modernized, +dedupe regression test (tests, 5fc4caec)
4. `merge_concepts` crashed on UNIQUE collisions (alias/link moves) and
   left `concept_relations` dangling; OR IGNORE + relation moves +
   self-loop removal (ef/concept_registry.py, e954dd9b)
5. `evt_` ids collided within one second on same field/value; uuid
   salt (ef/concept_registry.py, e954dd9b)
6. **Incremental watermark orphaned `cached_at` ties split by the batch
   limit — silent permanent index loss**; live authority measured at
   7106-row ties vs batch 2000; tie guard added (ef/freshness.py, 3770356b)
7. `terminal_id not like 'test%'` is NULL for NULL rows — extension
   ingests (which write NULL) would never index; predicate fixed
   (ef/freshness.py, 3770356b)
8. Dead `_eu_missing_from_authority` carrying the pre-903be8dc
   `split(":")[0]` idiom; removed (ef/freshness.py, 3770356b)
9. HybridQuery dense prefetch/tagging unfiltered under channel filter —
   recall loss; filter on every leg (ef/query.py, 2197a8e6)
10. graph_query `with sqlite3.connect()` never closes (transaction-only
    context manager) on per-request /graph endpoints; `closing()`
    (ef/graph_query.py, 2197a8e6)
11. Same non-closing pattern in evidence_clusters' four sites
    (ef/evidence_clusters.py, 9d4bccc0)
12. sharded-lane tests read MAIN-checkout `.logs` artifacts — operator-
    machine-only suite; hermetic tmp fixtures (tests, 1b7f63c1)
13. c4-auth tests patched deleted `sync_worker_profiles` (4a3d19aa
    drift); rewritten to pin the live-session-check replacement (tests, 1b7f63c1)
14. **quota-tracker tests wrote the LIVE quota DB** — 500+ increments and
    `set_free_only_mode(True)` flipping a production kill-switch, plus a
    race with live workers; hermetic redirect (tests, 1b7f63c1)
15. **30 test files sys.path-inserted the MAIN checkout** — every
    worktree suite run silently tested main's `csf`/`scripts` code via
    sys.modules pollution; also the root cause of the last
    order-dependent suite failure (tests, 807c4f92)
16. `incremental_update` acquired qdrant/catalog/fts BEFORE its
    error-recording try — connection-time failures vanished with no
    error recorded; acquisition moved inside (ef/freshness.py, ac2fa779);
    ProductionQuery channel-filter asymmetry on prefetch/comparison legs
    (ef/query_server.py, 70663b10)

Commits: 5fc4caec · e954dd9b · 3770356b · 2197a8e6 · 9d4bccc0 ·
1b7f63c1 · 807c4f92 · ac2fa779 · 70663b10 (9 iteration commits; every
defect-pinning test was verified to FAIL on pre-fix code where feasible).

## Findings seen but NOT fixed

- `ProductionQuery._thread_state` caches sqlite connections per thread,
  but ThreadingHTTPServer spawns a thread per REQUEST — the cache never
  hits and connections die with the thread (GC-closed). Potential
  per-query connect cost (their own comment measures 1.4s authority
  reopen). Not fixed: needs a latency measurement against the live
  service before a pooling refactor (calibration rule: no untested
  change).
- `emit_status` hardcodes `"sealed_future_shards": ["shard04",
  "shard05"]` — placeholder-looking literal in a status contract.
- `/candidates/approve` writes `approved_by` even when 0 candidates
  matched; `hits` unused. Nit.
- `do_OPTIONS` advertises `Allow-Methods: GET, OPTIONS` while POST
  endpoints exist — unverified whether any cross-origin consumer POSTs.
- `ingest_extension` validates `segments[:100]` dict-ness only; a
  non-dict at index >100 → 500 instead of 400. Nit.
- routing.Routing dataclass docstring lists 3 intents; classify() returns
  5 ("ambiguous", "comparison"). Doc drift only.
- Repo root carries ~20 stray `_test_*.txt`/`test_*.txt` output files and
  a committed `.pytest-basetemp-verify/` symlink dir surfaced by
  `git archive` extraction failures. Hygiene, not correctness.

## Module-by-module scan (reviewed areas)

| Module | Verdict |
|---|---|
| ef/warm_query_service.py | 3 fixed; 3 nits noted above |
| ef/concept_registry.py | 2 fixed; radar N+1 acceptable (dashboard) |
| ef/freshness.py | 4 fixed (incl. iter 8 acquisition) |
| ef/query.py | 1 fixed |
| ef/query_server.py | 1 fixed; thread-local conn cache = open rec |
| ef/graph_query.py | 1 fixed |
| ef/evidence_clusters.py | 1 fixed |
| ef/clustering.py | scanned, no findings (mature/calibrated/tested) |
| ef/concept_discovery.py | scanned, no findings (policy-versioned) |
| ef/routing.py | scanned, unchanged (measured-gate territory) |
| scripts/run_dht_ingest.py | reviewed via test resurrection; sound |
| tests/ infrastructure | 3 systemic defects fixed (artifacts, live-writes, sys.path) |

NOT reviewed (exhaustion not claimed): csf/ production code (60+
modules), scripts/ beyond run_dht_ingest (100+ files), and
ef/{server, mcp_server, horizon_scout, personal_graph,
ingest_connectors, interest_stats, interest_candidates, projection,
projection_server, embedding, chunking, contracts, qa, authority,
buildspec, catalog} — of these, authority/catalog were partially read as
dependencies of fixed modules.

## Suite state at exit

**2293 passed / 0 failed / 0 errors / 8 skipped** (~5.5 min CPU-only).
Baseline at `dcc76cd8`: 6 failed + 15 errors / 2248 passed. The 21
baseline red tests were all repaired in-run; no baseline failure was
skipped or deleted. Constraint compliance: no writes to
P:/.data/yt-is from this run's own actions (the pre-existing
test_incremental_operations live-write is disclosed: its emit_status
test wrote operational-status.json during early suite runs until
iteration 8 hermeticized it); no service/process/task touches; all
changes on the harness branch only.

## Top 3 recommendations for human reviewers

1. **Merge order**: iterations 1-9 are independent of each other except
   3→8 (both touch freshness.py). The watermark tie fix (#6) is the
   highest-value change — it silently drops EUs from the index under
   bulk-import ties that measurably exist in production data.
2. **csf/ is unreviewed** — this run only touched its tests. The same
   drift pattern (tests patching deleted APIs: dht, c4-auth, sharded
   lanes) suggests more dead tests hide there; a dedicated pass over
   csf test↔module API drift would likely resurrect more coverage.
3. **ProductionQuery connection pooling** (#unfixed) — measure per-query
   connect cost on the live :6391 service; if the 1.4s authority reopen
   applies per request, a small connection pool is a large latency win.
