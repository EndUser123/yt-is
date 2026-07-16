# yt-is Refactor Charter

**Status:** draft authority for worktree work (not a live-run authorization)  
**Created:** 2026-07-16  
**Last amended:** 2026-07-16 (reliability / anti-footgun promote policy)  
**Package:** `P:\packages\yt-is`  
**Baseline revision (at charter draft):** re-verify at worktree create (`git rev-parse HEAD` in package root)  
**Mode:** structure + control planes; **no** new throughput mechanism in the first slices

**Primary decision criterion (overrides merge-size preference):**  
**Reliability and anti-footgun.** Merge size is irrelevant. Do not promote transitional dual paths, silent failure modes, or half-cut seams that leave two sources of truth on `main`.

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

### G6 — Reverseability and isolation
- All structural work lands in a **git worktree + branch**, not on `main`.
- Worktree commits may be fine-grained for bisect; **promotes to main are gated by reliability invariants**, not by preferring small merges.
- Main stays production-capable until an explicit promote decision.

### G7 — Agent / operator navigability
- One module map and one current-contract pointer.
- Clear unit vs live test gates so agents do not burn NLM quota by accident.

### G8 — Anti-footgun (first-class)
After any promote to `main`, an operator or agent must not be able to:

- hit a **second parallel implementation** of mapping, status write, or path resolution by importing the “old” path;
- run a trial that **silently uses live DBs/browser roots** when a trial layout was intended;
- treat **order-only source mapping** as success;
- lose status rows via **swallowed exceptions**;
- confuse **unit** tests with **live** NLM runs.

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
8. Optimizing for small PR size or frequent merges to main.

---

## 5. Success criteria

### 5.1 Worktree slice complete (local)

A slice is done in the worktree when:

1. Characterization suite green for all C* that the slice claims (see §9).
2. No new dual-path left **inside the worktree tip** for that slice’s concern (or temporary shims are listed and time-boxed; see §11.2).
3. Claim ledger for the slice is filled (see §15).

### 5.2 Promote to main (reliability gate)

A promote to `main` is allowed only when **all** of the following hold for the promoted unit:

1. **Characterization green** on the exact tip revision being merged.
2. **Integrity invariants (always):**  
   - no order-only source↔video acceptance without corroboration (fail closed + structured event);  
   - `set_status_batch` never swallows row errors silently (log + count).
3. **Single source of truth:** no dual implementation of the promoted concern on `main` after merge (no “real logic in A, copy in B”; temporary re-export shims only if they add zero logic and are deleted in the same promote or the immediately next promote with an explicit debt line).
4. **No ambient live footgun introduced:** default paths and tests do not write trial data into live roots; live markers remain non-default.
5. **Import rule for any promote that includes harness boundary work:** `csf` does not import `harness` or `analyze`.
6. **Evidence freeze for moved events:** producer field names/units still match characterization/producer tests.
7. **No live benchmark** was used as the acceptance proof for the promote.
8. **Human promote decision** with the evidence packet in §14.

Self-review line required on handoff:  
`Self-review result: no blocking issues found` | `needs_fix`  
`Parent handoff: ready_for_parent_review` | `needs_fix` | `blocked` | `decision_required`

**Note:** “Full program done” (all slices) is **not** required for the first promote. The first promote is **integrity** (§16). Later promotes are **coherent structural units**, not “whatever is next in the file.”

---

## 6. Authority and doc pointers

During the worktree, treat these as authority (in order):

1. **This charter** (goals, freeze, slices, promote policy, non-goals)
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

- `@pytest.mark.characterization` — always run on refactor commits and before every promote  
- `@pytest.mark.unit` — default CI/local  
- `@pytest.mark.live` — requires explicit env allow + never default  

Command target (to add when implementing):

```text
pytest -m characterization
```

---

## 10. Integrity fixes (slice 0 — first commits)

Do these **before** large file moves.

### I1 — Source mapping fail-closed
- Remove or demote pure `zip(batch_ids, canonical_source_ids)` acceptance unless corroborated by URL/title/video_id extraction.
- Keep structured `nlm_batch_source_mapping_failed` (or successor) with counts and samples.
- Tests: C1–C3.

### I2 — Non-silent `set_status_batch`
- On per-row exception: log video_id + error; increment failure counter; never bare `pass`.
- Prefer returning `(ok_count, fail_count)` or equivalent without breaking all callers (compat shim OK **only if** it does not reintroduce silent success).
- Tests: C5.

---

## 11. Worktree execution plan (slices)

### 11.1 Preconditions
- Worktree root: **`P:/.worktrees`** (workspace canonical; not `P:/worktrees` for new work).
- Suggested worktree path: `P:/.worktrees/yt-is-refactor-control-planes`
- Suggested branch: `refactor/yt-is-control-planes`
- Create from current package `main`; re-record `HEAD` in this charter amendment log or a worktree README.
- Do not edit main for structural work while the worktree is active.
- No live NLM/benchmark without a separate decision packet and human authorization.

### 11.2 Shim rule (anti-footgun)
- Temporary re-export shims are allowed **inside the worktree** to keep imports green during a multi-commit extract.
- A promote that lands a seam on `main` must either:
  - **delete** parallel logic so only one implementation remains, or
  - keep a **zero-logic** re-export with an explicit debt line and a hard follow-up promote that removes it.
- **Forbidden on main:** two functions that both implement mapping/status/path policy.

### Slice 0 — Integrity + characterization skeleton
- I1, I2
- Add `@pytest.mark.characterization` tests; C1–C5 required for Slice 0 promote; C6–C8 if already cheap; C9–C10 when those slices land
- Green: `pytest -m characterization` (and relevant unit files)

### Slice 1 — State plane minimum
- Introduce `RuntimeLayout` / path helpers with live|staging|trial
- Route existing env overrides (`YTIS_TRANSCRIPT_CACHE_DB_PATH`, `YTIS_BATCH_STATUS_DB_PATH`, browser roots) through it
- Tests: C9; no behavior change for default live paths
- Promote only when path resolution has a **single** entry API for new code paths

### Slice 2 — Extract `nlm_batch` seams (behavior-preserving)
Order (low dependency first):

1. `mapping.py`
2. `cmd.py` / auth command runner
3. `source_add.py`
4. `content_fetch.py` (including retry queue gates)
5. `notebook_lifecycle.py`
6. Thin orchestrator left in industrial entry

Rules:

- Worktree may use temporary re-exports (see §11.2)
- No new knobs
- Event field contracts frozen; update characterization if moved
- **Promote policy for Slice 2:** prefer one promote that finishes the industrial seam set (or a complete subset with no dual logic), not a series of half-cut modules on `main`

### Slice 3 — Experiment plane
- Named profiles (`contract_current`, etc.) producing frozen config objects
- Run manifest writer (revision, profile, layout paths, resolved knobs)
- Start harness boundary: sharded/sweep modules may stay in place but must not be imported by core

### Slice 4 — Thin CLIs
- Peel logic out of `bin/csf-source` into `csf` modules behind command handlers
- Keep CLI surface identical
- Promote when CLI is thin **or** remaining bin code is pure argparse dispatch (no second business-logic copy)

### Slice 5 — Evidence plane hardening
- Schema constants / typed payloads for critical events
- Offline-only derived metrics documented
- Analyzer imports shared schema

### Optional later slices (not required for integrity promote)
- SQLite `schema_version` migrations for batch_status/cache
- Error enum taxonomy
- Delete deprecated `csf_nlm_ingest` after import audit
- Path portability; doc entropy cleanup
- Skill/CLI/doc matrix test

---

## 12. Commit policy (worktree)

- Prefer: one seam or one integrity fix per **commit** (bisect and review hygiene).
- Do not mix: “move mapping” + “change cliff timeout” in one commit.
- Message style: imperative, names the plane (`integrity:`, `refactor(nlm):`, `harness:`, `paths:`).
- After every commit: characterization green for applicable C*.
- **Merges to main** follow §14 / §16; do not split promotes merely to keep diffs small if that would leave dual paths on main.

---

## 13. Risk register

| Risk | Mitigation |
|------|------------|
| Wrong transcript after mapping change | Characterization C1–C3; fail closed |
| Silent status loss | I2 + C5 |
| Dual implementation after partial extract | §11.2 shim rule; promote only single-source units |
| Import cycles after split | Dependency order in Slice 2; smoke import tests |
| Harness accidentally imported by core | lint/grep gate: `csf` must not import `harness` |
| Env dual-alias confusion | Profiles resolve aliases once; log resolved config in run manifest |
| Live run during refactor | Non-goal; no packet → no run |
| Main contamination | Worktree-only edits; status check before any promote |
| Metric incomparability post-move | Event freeze + producer tests |
| Trial writes to live state | RuntimeLayout + C9; explicit trial roots |

---

## 14. Promotion gate (worktree → main)

Human decision required. Minimum evidence packet:

1. Worktree path + branch name + tip revision  
2. **Promote unit name** (e.g. `Promote-Integrity`, `Promote-Industrial-Seams`)  
3. List of commits (integrity vs pure move)  
4. Characterization results (command + pass on that tip)  
5. **Anti-footgun checklist** (see §14.1) all checked  
6. Confirmation: no live benchmark; no production config changed on main outside the merge  
7. Module map + import rule check when structure is included  
8. Known remaining debt (explicit; no silent dual paths)

Until that decision: **do not merge**, **do not** treat cleaner structure as production proof.

### 14.1 Anti-footgun checklist (required on every promote)

- [ ] No order-only mapping success path remains in the promoted code  
- [ ] No silent `pass` on status batch row failure  
- [ ] No second implementation of the promoted concern left on main  
- [ ] Defaults do not point trials at live DBs/browser roots  
- [ ] `@pytest.mark.live` (or equivalent) cannot run without explicit allow  
- [ ] Characterization green on tip  
- [ ] Debt lines (if any) name the next promote that removes them  

---

## 15. Claim ledger template (for each slice handoff)

| Claim | Type | Evidence | Falsifier | Action allowed |
|-------|------|----------|-----------|----------------|
| Characterization green on rev X | measured | pytest output | any required C* fail | continue / promote review |
| No behavior change except I1/I2 | inference until tests | diff + C* | regression in frozen contract | gather more tests |
| Single source of truth for concern Y | verified | grep/import + module map | second implementation found | fix before promote |
| Core does not import harness | verified | grep/import check | import edge found | fix before promote |
| Ready for live VPH comparison | unsupported by default | — | — | not allowed by this charter |

---

## 16. Decisions (resolved and open)

### 16.1 Resolved (2026-07-16)

| # | Decision | Resolution |
|---|----------|------------|
| D1 | Worktree root | **`P:/.worktrees`** for new work. Do not use `P:/worktrees` for this refactor unless an already-running process is pinned there. Suggested path: `P:/.worktrees/yt-is-refactor-control-planes`. |
| D2 | Slice 0 first | **Yes.** Integrity (I1/I2) + characterization before pure structural moves. |
| D3 | Promote scope / criterion | **Reliability and anti-footgun, not merge size.** See promote units below. |

### 16.2 Promote units (recommendation locked as policy)

| Promote unit | When | Contents | Why this unit |
|--------------|------|----------|----------------|
| **Promote-Integrity** (first merge to main) | After Slice 0 green | I1 + I2 + characterization for C1–C5 (and any C6–C8 already covered) | Removes **live production footguns** (wrong transcript, silent status loss) on the path operators actually run. Size of the merge is irrelevant. Holding integrity only in a worktree while main keeps order-zip **is** a reliability footgun. |
| **Promote-Industrial-Core** (second) | After Slices 1–2 form one coherent unit | RuntimeLayout (Slice 1) + full `nlm_batch` seam extract with **no dual logic** (Slice 2 complete or a complete subset that leaves zero parallel implementations) | Structural promote only when main cannot import the wrong copy. Prefer **one larger consistent merge** over a chain of half-cut modules on main. |
| **Promote-Experiment-Surface** (later) | After Slice 3 | Profiles, run manifest, harness import boundary | Trials become non-footgunning only when config is frozen and core ↛ harness. |
| **Promote-CLI-Evidence** (later) | After Slices 4–5 as needed | Thin CLI; event schema hardening | Operator/agent surface and metric honesty. |

**Explicitly rejected as promote policy:**

- Splitting promotes **only** to keep diffs small.  
- Promoting a single extracted file while the old body still implements the same policy.  
- Using live VPH to greenlight a structural promote.

### 16.3 Still open (non-blocking for worktree create)

1. Exact profile names for `contract_current` vs `hotel_wifi` vs guarded 2-lane (Slice 3).  
2. When to sunset dual env aliases (after profiles resolve them once).  
3. Whether `Promote-Industrial-Core` includes moving sharded modules into `harness/` in the same merge or leaves them in place with import rules only.

---

## 17. Immediate next actions

1. Human accepts this amended charter (or further amends).  
2. When authorized: create worktree at `P:/.worktrees/yt-is-refactor-control-planes` from package main; pin base revision.  
3. Execute Slice 0 (integrity + characterization).  
4. Run anti-footgun checklist; prepare **Promote-Integrity** evidence packet; human decides merge to main.  
5. Continue Slices 1–2 in the same worktree toward **Promote-Industrial-Core** (no obligation to merge early for size reasons).

---

## 18. Amendment log

| Date | Change | Author |
|------|--------|--------|
| 2026-07-16 | Initial draft from whole-package review + gap→opportunity map | agent draft |
| 2026-07-16 | Reliability/anti-footgun as primary promote criterion; resolve D1–D3; promote units; shim rule; G8; reject merge-size optimization | agent amend (human direction) |

---

**End of charter.**  
This document authorizes **planning and worktree structural work** only after human approval. It does **not** authorize live NotebookLM benchmarks, production path changes on main outside an explicit promote, or unattended promotion to main.
