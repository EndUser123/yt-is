# yt-is Refactor Charter

**Status:** draft authority for worktree work (not a live-run authorization)  
**Created:** 2026-07-16  
**Package:** `P:\packages\yt-is`  
**Baseline revision (at charter draft):** `96c4583` (re-verify at worktree create)  
**Mode:** structure + control planes; **no** new throughput mechanism in the first slices

---

## 1. One-sentence purpose

Make the industrial pipeline **maintainable** and **trial-friendly** by separating code, state, experiment, and evidence planes—without increasing silent wrong-data risk, and without changing production behavior except for explicit integrity fixes.

---

## 2. Problem statement

| Symptom | Root shape |
|---------|------------|
| Hard to change one concern without reading multi-kLOC functions | Gravity into `csf/nlm_batch.py` (~6k) and `bin/csf-source` (~4k) |
| Trials are expensive and confounded | Env soup (~76 `YTIS_*` knobs), scattered state roots, harness mixed into core |
| Metrics can lie | ~122 log events; process checklist exists; no machine-checked schema freeze |
| Agents and humans mis-operate | Skills/docs/CLI drift; package `.data` vs `P:\.data`; live vs unit unclear |

This is not primarily a “style” refactor. It is a **control-system** refactor.

---

## 3. Goals

### G1 — Maintainability
- Production modules have a single job and stay reviewable.
- A change to mapping, fetch, auth, status, or CLI does not require editing the others.

### G2 — Trial / experiment support
- A trial is a **profile + harness + cohort + run manifest**, not a new branch inside the hot path.
- Adding a trial does not require editing source→video mapping or status persistence.

### G3 — Data integrity (explicit, not implied)
- Prefer fail-closed over plausible wrong transcripts.
- Prefer logged failures over silent `pass` in bulk status writes.

### G4 — Behavioral freeze (except G3 integrity)
- Routing split, promote fail-closed, complete-never-downgrades, and public CLI names stay stable unless a charter amendment says otherwise.

### G5 — Four-plane control system
| Plane | Purpose |
|-------|---------|
| **Code** | Small modules, clear owners, core ↛ harness |
| **State** | One layout for DBs, browsers, notebooks, locks; live vs staging vs trial |
| **Experiment** | Named profiles, harness runners, cohorts, abort/promotion |
| **Evidence** | Stable event contracts, offline analyzers, registry/packets |

### G6 — Reverseability
- All structural work lands in a **git worktree + branch**, not on `main`.
- Small commits; main stays production-capable until an explicit promote decision.

### G7 — Agent / operator navigability
- One module map and one current-contract pointer.
- Clear unit vs live test gates so agents do not burn NLM quota by accident.

---

## 4. Non-goals (first worktree program)

Do **not** include in the initial slices:

1. New throughput mechanisms (new cliff math, new projection knobs, new worker topology).
2. Live same-shape VPH benchmarks as refactor acceptance criteria.
3. Full rewrite of the transcript fallback chain (Selenium/Whisper/yt-dlp) in the same branch as NLM industrial split.
4. Enterprise packaging (multi-tenant, DI frameworks, plugin systems) unless a concrete coupling forces it.
5. Bulk documentation entropy purge (archive only when it blocks authority).
6. Full path portability off `P:\` (may follow; not the first gate).
7. Optimizing reducers before the producer/core boundary is stable.

---

## 5. Success criteria (“done enough to promote”)

A slice or the overall program is promotion-ready only when:

1. **Characterization suite green** (see §9) on the worktree revision.
2. **Integrity:** no order-only source mapping acceptance without corroboration *or* hard fail + structured event; `set_status_batch` never swallows errors silently.
3. **Module map** exists and matches imports (no core→harness dependency).
4. **RunConfig / profile** can freeze knobs for a trial without editing hot-path files.
5. **Critical event contracts** for moved code still match producer tests (field names/units).
6. **Main untouched** by the structural commits (worktree-only) until promote.
7. **No new live benchmark** was run without a decision packet (charter does not authorize live runs).

Self-review line required on handoff:  
`Self-review result: no blocking issues found` | `needs_fix`  
`Parent handoff: ready_for_parent_review` | `needs_fix` | `blocked` | `decision_required`

---

## 6. Authority and doc pointers

During the worktree, treat these as authority (in order):

1. **This charter** (goals, freeze, slices, non-goals)
2. `AGENTS.md` (throughput gates, claim ledger, parent handoff)
3. `docs/operations/observability-contract-checklist.md`
4. `docs/operations/test-registry.md` + decision-packet templates
5. `HANDOFF.md` as **historical ops context**, not a license to launch benchmarks

If two docs conflict: stop, amend the charter, do not silently merge.

---

## 7. Behavioral freeze list

| Contract | Freeze rule |
|----------|-------------|
| Public CLI entrypoints | `yt-is`, `csf-source`, `csf-nlm`, promote/backup/migrate names remain |
| Routing split | live/premiere → `transcript_fallback`; captioned + `no_captions` → NotebookLM (unless integrity requires fail-closed elsewhere) |
| Status monotonicity | never downgrade `complete` |
| Promote/backup | fail-closed on missing/empty/collision |
| Video ID validation | 11-char pattern remains at boundaries |
| Event **names** for industrial hot path | freeze during moves; version if meaning changes (`...@v2`) |
| Derived metrics | computed offline; no new tautological in-process “proof” fields |

**Allowed intentional behavior changes (G3 only), each with tests:**

- Reject ambiguous source↔video mapping (fail closed)
- Log + count per-row failures in bulk status writes

---

## 8. Target architecture (end state sketch)

```text
csf/                          # production core (no harness imports)
  paths.py / runtime_layout   # state plane roots (live|staging|trial)
  nlm_config.py + profiles    # frozen RunConfig / named profiles
  nlm/
    auth.py                   # from nlm_batch + nlm_auth_guard + worker_auth
    cmd.py                    # _run_cmd / subprocess boundary
    mapping.py                # source list → video_id (fail-closed)
    source_add.py             # add + subbatch
    content_fetch.py          # fetch + retry queue gates
    notebook_lifecycle.py     # create/recycle/cleanup
    industrial.py             # thin orchestrator (process_batch / extract)
  batch_status.py             # status + channel state (migrate toward schema_version)
  cache.py                    # transcript cache
  transcript.py               # fallback chain (later split if needed)
  ...

harness/                      # experiments only (may import csf)
  profiles/
  cohorts/
  sharded_lane_*.py           # moved from csf/ when safe
  runners/

analyze/                      # offline only
  scripts already in scripts/ may move here over time

bin/                          # thin CLIs dispatching to csf/harness
```

**Import rule:** `csf` must not import `harness` or `analyze`.  
**CLI rule:** `bin/*` stay thin; no multi-kLOC business logic in bin long-term.

Exact filenames may adjust; the **seams** are the contract.

---

## 9. Characterization suite (must stay green)

Minimum set (names illustrative; implement as real tests/markers):

| ID | Behavior under test |
|----|---------------------|
| C1 | Mapping: exact title/url match accepted |
| C2 | Mapping: ambiguous / partial order-only → fail closed + event |
| C3 | Mapping: duplicate source_id → fail closed |
| C4 | Status: complete never downgrades |
| C5 | Status: batch write logs/counts row failures (no silent pass) |
| C6 | Promote transcripts: missing/empty/same-path refused |
| C7 | Promote channel state: same fail-closed class |
| C8 | Routing: live → fallback; no_captions → NLM (unit-level) |
| C9 | Path layout: trial/staging env overrides do not touch live defaults in tests |
| C10 | Config: named profile freezes batch size / source cap / cliff-related knobs |

Marker convention (target):

- `@pytest.mark.characterization` — always run on refactor commits  
- `@pytest.mark.unit` — default CI/local  
- `@pytest.mark.live` — requires explicit env allow + never default  

Command target (to add when implementing):

```text
pytest -m characterization
```

---

## 10. Integrity fixes (slice 0 — first commits)

Do these **before** large file moves when possible.

### I1 — Source mapping fail-closed
- Remove or demote pure `zip(batch_ids, canonical_source_ids)` acceptance unless corroborated by URL/title/video_id extraction.
- Keep structured `nlm_batch_source_mapping_failed` (or successor) with counts and samples.
- Tests: C1–C3.

### I2 — Non-silent `set_status_batch`
- On per-row exception: log video_id + error; increment failure counter; never bare `pass`.
- Prefer returning `(ok_count, fail_count)` or equivalent without breaking all callers (compat shim OK).
- Tests: C5.

---

## 11. Worktree execution plan (slices)

### Preconditions
- Create worktree + branch from current `main` (or agreed base).
- Re-record `HEAD` in this charter or a worktree README.
- Do not edit main for structural work while the worktree is active.
- No live NLM/benchmark without a separate decision packet and human authorization.

### Slice 0 — Integrity + characterization skeleton
- I1, I2
- Add `@pytest.mark.characterization` tests C1–C10 (stubs OK only if behavior already covered; prefer real asserts)
- Green: `pytest -m characterization` (and relevant unit files)

### Slice 1 — State plane minimum
- Introduce `RuntimeLayout` / path helpers with live|staging|trial
- Route existing env overrides (`YTIS_TRANSCRIPT_CACHE_DB_PATH`, `YTIS_BATCH_STATUS_DB_PATH`, browser roots) through it
- Tests: C9; no behavior change for default live paths

### Slice 2 — Extract `nlm_batch` seams (behavior-preserving)
Order (low dependency first):

1. `mapping.py`
2. `cmd.py` / auth command runner
3. `source_add.py`
4. `content_fetch.py` (including retry queue gates)
5. `notebook_lifecycle.py`
6. Thin orchestrator left in industrial entry

Rules:

- Move + re-export shims if needed to keep imports stable mid-slice
- No new knobs
- Event field contracts frozen; update characterization if moved

### Slice 3 — Experiment plane
- Named profiles (`contract_current`, etc.) producing frozen config objects
- Run manifest writer (revision, profile, layout paths, resolved knobs)
- Start harness boundary: sharded/sweep modules may stay in place but must not be imported by core

### Slice 4 — Thin CLIs
- Peel logic out of `bin/csf-source` into `csf` modules behind command handlers
- Keep CLI surface identical

### Slice 5 — Evidence plane hardening
- Schema constants / typed payloads for critical events
- Offline-only derived metrics documented
- Analyzer imports shared schema

### Optional later slices (not required for first promote)
- SQLite `schema_version` migrations for batch_status/cache
- Error enum taxonomy
- Delete deprecated `csf_nlm_ingest` after import audit
- Path portability; doc entropy cleanup
- Skill/CLI/doc matrix test

---

## 12. Commit policy (worktree)

- Prefer: one seam or one integrity fix per commit.
- Do not mix: “move mapping” + “change cliff timeout” in one commit.
- Message style: imperative, names the plane (`integrity:`, `refactor(nlm):`, `harness:`, `paths:`).
- After every commit: characterization green.

---

## 13. Risk register

| Risk | Mitigation |
|------|------------|
| Wrong transcript after mapping change | Characterization C1–C3; fail closed |
| Silent status loss | I2 + C5 |
| Import cycles after split | Dependency order in Slice 2; smoke import tests |
| Harness accidentally imported by core | lint/grep gate: `csf` must not import `harness` |
| Env dual-alias confusion | Profiles resolve aliases once; log resolved config |
| Live run during refactor | Non-goal; no packet → no run |
| Main contamination | Worktree-only edits; status check before any promote |
| Metric incomparability post-move | Event freeze + producer tests |

---

## 14. Promotion gate (worktree → main)

Human decision required. Minimum evidence packet:

1. Worktree branch name + tip revision  
2. List of commits (integrity vs pure move)  
3. Characterization results (command + pass)  
4. Confirmation: no live benchmark; no production config changed  
5. Module map + import rule check  
6. Known remaining debt (explicit)  

Until that decision: **do not merge**, **do not** treat cleaner structure as production proof.

---

## 15. Claim ledger template (for each slice handoff)

| Claim | Type | Evidence | Falsifier | Action allowed |
|-------|------|----------|-----------|----------------|
| Characterization green on rev X | measured | pytest output | any C* fail | continue / promote review |
| No behavior change except I1/I2 | inference until tests | diff + C* | regression in frozen contract | gather more tests |
| Core does not import harness | verified | grep/import check | import edge found | fix before promote |
| Ready for live VPH comparison | unsupported by default | — | — | not allowed by this charter |

---

## 16. Open decisions (need human input before or during work)

1. Worktree location/name convention (`P:\worktrees\...` vs package `worktrees/`).  
2. Whether Slice 0 integrity (stricter mapping) is acceptable **before** any pure split (recommended: yes).  
3. Whether first promote is “integrity only” or “integrity + partial nlm_batch split.”  
4. Profile names for current contract vs hotel_wifi vs guarded 2-lane.  
5. When (if ever) to sunset dual env aliases.

---

## 17. Immediate next actions

1. **Approve or amend this charter** (human).  
2. Resolve open decisions §16.1–16.3.  
3. Create worktree + branch; pin base revision here.  
4. Execute Slice 0 (integrity + characterization).  
5. Stop for parent/human review before Slice 2 bulk moves if Slice 0 changes behavior.

---

## 18. Amendment log

| Date | Change | Author |
|------|--------|--------|
| 2026-07-16 | Initial draft from whole-package review + gap→opportunity map | agent draft |

---

**End of charter.**  
This document authorizes **planning and worktree structural work** only after human approval. It does **not** authorize live NotebookLM benchmarks, production path changes on main, or promotion to main.
