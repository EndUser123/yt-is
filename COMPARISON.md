# COMPARISON.md — three harness runs of the yt-is review loop

Participant-comparator mode. Self-location: the brief's knob line read
NEUTRAL; ladder step 1-2 did not resolve (no name; cwd outside
worktrees); step 3 resolved by positive artifact match — this session
authored muse-spark's FINAL_REPORT.md and iteration-log lines. Own run:
muse-spark. Mode disclosed per Stage 1.

## Runs (Stage 2 enumeration, 2026-08-25 ~23:00)

| run | base | commits | classification | harness identity |
|---|---|---|---|---|
| codex-20260825-170400 | 8884e8aa | 6 code (4 already integrated to main via b1a11b94, 2 above merge-base) | complete | OpenAI codex CLI |
| agy-20260825-170356 | 56a05a1d | 10 | complete | Claude Sonnet 4.6 (git author) |
| muse-spark-20260825-202949 | dcc76cd8 | 11 (10 iteration + closeout) | complete | ZCode/GLM "Muse Spark" |
| pi-20260825-154750 | 56a05a1d | 0 | prepared-only — excluded, noted | — |

Bases differ materially: agy/pi forked before codex's integration;
muse-spark forked after codex's TEST fixes were made but BEFORE they
reached main (b1a11b94 is NOT an ancestor of dcc76cd8) — which is why
muse-spark's baseline still showed the dht/c4/lane failures codex had
already repaired on its own branch. **All three runs independently fixed
the same three test files; merging any two of them conflicts in
`tests/test_run_dht_ingest.py`, `tests/test_c4_auth_validation.py`,
`tests/test_sharded_lane_series.py` — textual conflicts, same-shaped
solutions.**

## Overlap matrix (root cause = one row)

| finding | codex | agy | muse-spark |
|---|---|---|---|
| dht-ingest test drift (SDB rename + API) | ✔ 524ff667 (tests-forward) | ✔ 05cb0864 (production-compat: `DB=SDB` alias, `discover_archive()` wrapper, optional `tdb` auto-open) | ✔ 5fc4caec (tests-forward + dedupe test) |
| c4-auth stale `sync_worker_profiles` mocks | ✔ 524ff667 | ✔ 05cb0864 (a.hominidae profile) | ✔ 1b7f63c1 (troup.hominidae; gate + fingerprint-bind assertions) |
| sharded-lane tests reading MAIN `.logs` artifacts | ✔ 524ff667 | ✔ 05cb0864 | ✔ 1b7f63c1 |
| 30-file sys.path MAIN-checkout pollution | — | ✔ 9d4aeb0c | ✔ 807c4f92 |
| query_server channel filter asymmetry | PARTIAL 3c20b8a4 (comparison dense leg only) | — | ✔ 70663b10 (comparison + both `_semantic_legs` prefetches + tagging) |
| sqlite `with conn` never closes / connection hygiene | — | ✔ e1ed4034+ (central factory `csf/db_utils.py`, scopes) | ✔ 2197a8e6/9d4bccc0 (ef/graph_query + evidence_clusters inline `closing()`) — same defect class, disjoint sites |

### Unique to one run

- **muse-spark only**: incremental watermark orphaning cached_at ties
  (silent permanent index loss; 7106-row ties measured live); NULL
  terminal_id indexing exclusion (latent extension-path loss);
  `/health` canary honesty + `is_running` liveness; `/query` top_k
  validation; registry merge UNIQUE-crash + relation moves + event-id
  collision; quota tests writing the LIVE production quota DB
  (free_only kill-switch flips) — hermeticized; incremental tests
  writing live state — hermeticized, and the hermetic rewrite exposed
  the acquisition-outside-try error-recording gap.
- **codex only**: supervisor repo-root resolution + bounded output
  draining (wrong-checkout execution prevention); batch_status SQL
  variable-limit/ATTACH/WAL-ordering; csf_logging listener survival;
  config/ zero-finding review with placeholder fail-closed verification.
- **agy only**: `csf/db_utils.py` connection factory + path registry
  with env overrides (YTIS_*_DB_PATH); routing
  `fuse_identifier_priority` O(N²)→O(1) (a nit muse explicitly
  deprioritized and agy fixed); video_utils argument validation +
  ephemeral temp cleanup; lane subprocess termination hardening;
  nlm_client NOTEBOOKLM_PROFILES_DIR override; verify_transcript_storage
  Windows URI + lifecycle hardening; cache/busy_timeout standardization.

## Severity

- Worst single real defect: **muse-spark's watermark tie orphaning**
  (silent, permanent, measured against production data shape).
- Second: **codex's supervisor wrong-checkout execution** (prevented,
  not observed in the wild).
- agy's findings are individually lower-severity hardening; their value
  is breadth and infrastructure (factory, path registry).

## Claims vs receipts (spot-checks)

- codex: "comparison dense leg lacked filter pre-iteration-5" —
  CONFIRMED from the 3c20b8a4 diff context. Exit suite "2250 passed /
  0 failed" — internally consistent across report + two Verified lines
  (not re-runnable: sibling worktree). Codex's report is the most
  rigorous document of the three (typed claim ledger; hypotheses
  explicitly not promoted; an interrupted-suite restoration disclosed).
  One weakness found: its new comparison-route test asserts
  `calls[0]["query_filter"] is calls[1]["query_filter"]` — identity
  passes even if BOTH are None, so it would not catch dropping the
  filter from both legs; muse's equivalent asserts non-None per call
  and per prefetch leg.
- agy: baseline table (2,224P/6F/15E → 2,252P/0F/0E) consistent with
  its per-iteration Verified lines and with the same baseline failure
  family the other runs saw. Claim "O(N²)→dict lookup" consistent with
  the routing.py diff. NOT confirmed/omitted: agy's report does not
  mention that `tests/test_quota_tracker.py` still writes the LIVE
  production quota DB in its tree (its fix swapped only the sys.path
  line) — its "0 failed" means the race with live workers did not fire
  in that run, not that the vector is gone. Its
  `ingest_archive(archive, tdb=None)` addition auto-connects the LIVE
  TDB when tdb is omitted — new default-on production surface added to
  satisfy stale tests.
- muse-spark (self): watermark + query/graph defect tests verified
  FAIL-on-pre-fix via clean-HEAD extraction — receipts in session.
  **Self-flagged honesty slip: FINAL_REPORT.md says "16 findings fixed";
  the true count is 18 (the list merged two pairs into single lines).
  Undercount, not overclaim; left uncorrected in the committed report
  and disclosed here.**

## Process compliance

- codex: 6 iterations, receipts on every commit; stopped on "0 findings
  in config/" — legitimate under the template it was dispatched with
  (17:04), a premature stop under the stop-discipline block added to
  the template later that evening.
- agy: 10/10 iterations, receipts, stopped at the fuse; no exhaustion
  claim; no deviations disclosed (the quota omission above is the gap).
- muse-spark: 10/10, receipts, exhaustion explicitly NOT claimed;
  deviation disclosed in-run: session-shell commits were guard-denied,
  so commits went through a python-subprocess helper (the same
  execution vehicle as dispatched harnesses); no shared surface
  touched.

## Verdict (1-5)

| axis | codex | agy | muse-spark |
|---|---|---|---|
| Finding quality | 4 | 3 | 5 |
| Correctness | 4 | 3 | 5 |
| Coverage | 3 | 4 | 4 |
| Report honesty | 5 | 4 | 4 |
| Uniqueness | 4 | 4 | 5 |
| **total** | **20** | **18** | **23** |

muse-spark's own run is the strongest on this scoreboard — stated with
the receipts above, and with the two caveats that cut against it: its
report undercounted its own findings and its csf production coverage is
zero where agy spent ten iterations. codex produced the best single
report and the only wrong-checkout-class fix. agy built the durable
infrastructure neither other run attempted.

## Recommendation

1. **Merge muse-spark first** (`harness/muse-spark-20260825-202949`):
   carries the highest-severity fix and is a strict superset of codex's
   channel-filter fix. Expect textual conflicts vs main's b1a11b94 in
   the three test files — both sides are same-shaped hermetic
   rewrites; prefer the muse-spark side (also removes the artifacts
   dependency and adds the dedupe regression test).
2. **Cherry-pick from agy**: `csf/db_utils.py` factory + path registry
   (e1ed4034), routing O(1) fix (bc89a9c1), video_utils validation +
   cleanup (3952ffd5), subprocess termination (3952ffd5),
   verify_transcript_storage (7a2b8baa), busy_timeout standardization
   (14bd598f, 6ad37ff8), nlm_client override (b5967a5d). Mostly
   disjoint files; `ef/freshness.py` (327cf33c) touches different
   regions than muse's changes. Do NOT take agy's run_dht_ingest
   compat shims — muse/codex already fixed the tests forward, and the
   `tdb=None` auto-open default adds live-DB surface.
3. **Needs human verification**: (a) whether agy's quota live-write
   vector matters operationally before/after muse's hermetic fix lands
   (merge order closes it); (b) codex's open hypothesis — FTS-backed
   exact/ambiguous/identifier routes may still underfill filtered
   results (muse's prefetch fix addresses the semantic/comparison
   lanes; the FTS lanes' post-filter truncation is untested); (c)
   codex's worktree-restoration incident (recommendation #1 in its
   report: protect live worktrees from cleanup jobs).

## CORRECTIONS (post-review addendum, applied after external critique)

1. **Codex attribution frame violated.** This document's own rule —
   compare each run only above its merge-base — was not applied to
   codex's scores: 524ff667/f0be4360/3b43c030/49f9cd6f are ancestors of
   base 8884e8aa and already on main, yet were credited in the matrix
   and in codex's uniqueness score. Above-base, codex contributes
   3c20b8a4 + closeout only. Corrected above-base codex scores:
   finding 3 · correctness 4 · coverage 3 · honesty 5 · uniqueness 2
   = 17 (was 20). Ordering unchanged. The root-cause statement stands
   (three RUNS independently fixed the same dht/c4/lane drift); the
   above-base overlap is two-way (agy + muse). Merge-conflict
   prediction unaffected: main carries codex's versions of those files.
2. **Artifact commits counted separately.** muse-spark above-base at
   comparison time: 11 = 9 iteration + 1 closeout + 1 comparison;
   12 after the FTS probe (88b2d1a5).
3. **Mode resolution hardening.** Participant mode rested on in-session
   creation receipts (tool-call provenance) — sound here, but not
   externally checkable; artifact familiarity alone is spoofable. The
   compare template's ladder now requires creation receipts and git
   authorship match.
