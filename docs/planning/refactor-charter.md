# yt-is Refactor Charter

**Status:** draft authority for worktree work (not a live-run authorization)  
**Created:** 2026-07-16  
**Last amended:** 2026-07-16 (P0/P1 critical-review fixes: pairing ranks, canary/rollback, promote split, shim/characterization/git)  
**Package:** `P:\packages\yt-is` (own git repo; not the `P:\` monorepo root)  
**Baseline revision:** re-verify at worktree create: `git -C P:/packages/yt-is rev-parse HEAD`  
**Mode:** structure + control planes; **no** new throughput mechanism in the first slices

**Primary decision criterion:**  
**Reliability and anti-footgun** (correctness **and** availability). Merge size is irrelevant. Do not promote transitional dual paths, silent failure modes, or half-cut seams that leave two sources of truth on `main`.

---

## 0. Execution card (read this first)

| Item | Value |
|------|--------|
| **Repo** | `P:/packages/yt-is` only (`git worktree` from this repo) |
| **Worktree** | `P:/.worktrees/yt-is-refactor-control-planes` |
| **Branch** | `refactor/yt-is-control-planes` |
| **Order** | Slice 0 → Promote-Integrity (+ canary) → Slice 1 → Promote-Paths → Slice 2 → Promote-Industrial-Seams → later optional |
| **First behavior change** | Mapping ranks C/D fail closed; status batch no silent `pass` (§10) |
| **Required tests for Promote-Integrity** | C1–C5 only (§9.1); **new asserts**, not retags of order-fallback-success tests |
| **Forbidden** | Live VPH as acceptance; dual policy implementations on main; zero-logic “next promote” shim debt; monorepo-root worktree |
| **Stop / rollback** | §10.3 canary fail or mapping_failed / status anomaly thresholds |
| **Charter edits during work** | Prefer in the **worktree** copy; merge with Promote-Integrity or keep worktree-authoritative until then |

---

## 1. One-sentence purpose

Make the industrial pipeline **maintainable** and, over later slices, **trial-friendly**, by separating code, state, experiment, and evidence planes—reducing silent wrong-data risk, without treating “more fail-closed” as free (availability is part of reliability).

**Honest scope note:** Promote-Integrity and Promote-Paths do **not** by themselves make trials easy. Trial ergonomics land with Experiment-surface work (Slice 3+).

---

## 2. Problem statement

| Symptom | Root shape |
|---------|------------|
| Hard to change one concern without reading multi-kLOC functions | Gravity into `csf/nlm_batch.py` (~6k) and `bin/csf-source` (~4k) |
| Trials are expensive and confounded | Env soup (~76 `YTIS_*` knobs), scattered state roots, harness mixed into core |
| Metrics can lie | ~122 log events; process checklist exists; no machine-checked schema freeze |
| Agents and humans mis-operate | Skills/docs/CLI drift; package `.data` vs `P:\.data`; live vs unit unclear |

This is not primarily a “style” refactor. It is a **control-system** refactor.

**Epistemic note on integrity:** Order-based pairing **code paths exist** (verified). How often they cause **wrong transcripts in production** is **not proven** in this charter (hypothesis / risk). Slice 0 is **risk reduction under incomplete evidence**, not a claim of measured production misattribution rate. Prefer a log baseline and post-promote canary (§10.3) over slogans.

---

## 3. Goals

### G1 — Maintainability
- Production modules have a single job and stay reviewable.
- A change to mapping, fetch, auth, status, or CLI does not require editing the others.

### G2 — Trial / experiment support (later slices)
- A trial is a **profile + harness + cohort + run manifest**, not a new branch inside the hot path.
- Adding a trial does not require editing source→video mapping or status persistence.

### G3 — Data integrity (explicit, not implied)
- Prefer fail-closed for **uncorroborated** source↔video pairing (ranks C/D in §10.1).
- Prefer logged failures over silent `pass` in bulk status writes.
- Do not ban **corroborated** pairing (ranks A/B) without evidence that they are unsafe.

### G4 — Behavioral freeze (except documented integrity changes)
- Routing split, promote fail-closed, complete-never-downgrades, and public CLI names stay stable unless a charter amendment says otherwise.
- **Exception:** mapping acceptance policy changes in Slice 0 per §10.1 (ranks C/D rejected). This is an intentional production behavior change and may increase `mapping_failed` rates.

### G5 — Four-plane control system (diagnosis + target; full build optional)
| Plane | Purpose |
|-------|---------|
| **Code** | Small modules, clear owners, core ↛ harness |
| **State** | One layout for DBs, browsers, notebooks, locks; live vs staging vs trial |
| **Experiment** | Named profiles, harness runners, cohorts, abort/promotion |
| **Evidence** | Stable event contracts, offline analyzers, registry/packets |

Full four-plane implementation is **not** required for “refactor succeeded enough” (see §5.3 MVP).

### G6 — Reverseability and isolation
- All structural work lands in a **git worktree + branch** of **`P:/packages/yt-is`**, not on `main`, not from monorepo `P:\` root.
- Worktree commits may be fine-grained for bisect; **promotes to main are gated by reliability invariants**, not merge size.
- Main stays production-capable until an explicit promote decision.

### G7 — Agent / operator navigability
- One module map and one current-contract pointer (MVP+).
- Clear unit vs live test gates so agents do not burn NLM quota by accident.

### G8 — Anti-footgun (first-class)
After any promote to `main`, an operator or agent must not be able to:

- hit a **second parallel implementation** of mapping, status write, or path resolution by importing the “old” path;
- run a trial that **silently uses live DBs/browser roots** when a trial layout was intended;
- treat **rank C/D** source mapping as success;
- lose status rows via **swallowed exceptions**;
- confuse **unit** tests with **live** NLM runs.

### G9 — Availability is part of reliability
- Fail-closed pairing that stalls the industrial backlog without a measured benefit is a **failed** integrity change.
- Promote-Integrity requires canary/rollback criteria (§10.3), not only green unit tests.

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
9. Claiming measured production misattribution rates without log/canary evidence.

---

## 5. Success criteria

### 5.1 Worktree slice complete (local)

A slice is done in the worktree when:

1. **Required C\* for that slice** are green (see §9.1)—not the entire C1–C10 list unless claimed.
2. No dual **policy** implementation left for that slice’s concern at tip (see §11.2 shims).
3. Claim ledger for the slice is filled (see §15).

### 5.2 Promote to main (reliability gate)

A promote to `main` is allowed only when **all** of the following hold for the promoted unit:

1. **Required characterization set** green on the exact tip revision (§9.1).
2. **Integrity invariants (always, once Slice 0 has landed on main or is part of the promote):**  
   - pairing follows §10.1 (C/D fail closed; A/B allowed only under stated invariants);  
   - `set_status_batch` never swallows row errors silently (log + count; caller policy §10.2).
3. **Single source of truth for policy:** no second implementation of the promoted concern’s **rules** on `main`. Zero-logic re-exports of public import paths are allowed only per §11.2 (no “fix next promote” escape hatch for dual policy).
4. **No ambient live footgun introduced:** default paths and tests do not write trial data into live roots; live markers remain non-default.
5. **Import rule for any promote that includes harness boundary work:** `csf` does not import `harness` or `analyze`.
6. **Evidence freeze for events touched by the promote:** producer field names/units still match tests (do not wait for Slice 5 to freeze fields you change now).
7. **No live VPH benchmark** was used as the acceptance proof for the promote.
8. **Human promote decision** with the evidence packet in §14.
9. For **Promote-Integrity only:** baseline note + canary plan per §10.3 (canary may complete after merge if explicitly staged; rollback criteria must exist **before** merge).

Self-review line required on handoff:  
`Self-review result: no blocking issues found` | `needs_fix`  
`Parent handoff: ready_for_parent_review` | `needs_fix` | `blocked` | `decision_required`

### 5.3 MVP end-state (“refactor succeeded enough to stop”)

The program may stop without finishing every plane when **all** of the following are on `main`:

1. Promote-Integrity landed and canary not rolled back.  
2. Promote-Paths landed (single path API; C9).  
3. Promote-Industrial-Seams landed for at least: **mapping**, **content_fetch** (incl. retry gates), **auth/cmd**, thin orchestrator entry—with **no dual policy**.  
4. Module map document matches imports.  
5. Characterization required sets for those promotes stay green.

**Optional after MVP:** full notebook_lifecycle extract, harness/ directory move, profiles/manifest, thin entire `csf-source`, event schema package, schema_version migrations, auth lifecycle hardening track (§16.4).

---

## 6. Authority and doc pointers

During the worktree, treat these as authority (in order):

1. **This charter** (goals, freeze, slices, promote policy, non-goals)  
2. `AGENTS.md` (throughput gates, claim ledger, parent handoff)  
3. `docs/operations/observability-contract-checklist.md`  
4. `docs/operations/test-registry.md` + decision-packet templates  
5. `HANDOFF.md` as **historical ops context**, not a license to launch benchmarks  

If two docs conflict: stop, amend the charter, do not silently merge.

**Charter file location:** `docs/planning/refactor-charter.md` inside the **yt-is** repo. While the worktree is active, treat the **worktree copy** as the working authority for ongoing amendments; do not maintain a divergent long-lived edit only on `main`.

---

## 7. Behavioral freeze list

| Contract | Freeze rule |
|----------|-------------|
| Public CLI entrypoints | `yt-is`, `csf-source`, `csf-nlm`, promote/backup/migrate names remain |
| Routing split | live/premiere → `transcript_fallback`; captioned + `no_captions` → NotebookLM |
| Status monotonicity | never downgrade `complete` |
| Promote/backup | fail-closed on missing/empty/collision |
| Video ID validation | 11-char pattern remains at boundaries |
| Event **names** for industrial hot path | freeze during moves; version if meaning changes (`...@v2`) |
| Derived metrics | computed offline; no new tautological in-process “proof” fields |
| **Mapping acceptance** | **Not frozen** for Slice 0: changes to §10.1 ranks (C/D fail closed) |

**Allowed intentional behavior changes (Slice 0), each with tests:**

- Reject rank C/D pairing (fail closed + structured event)  
- Log + count per-row failures in bulk status writes; define caller visibility (§10.2)

---

## 8. Target architecture (end state sketch)

```text
csf/                          # production core (no harness imports)
  paths.py / runtime_layout   # state plane roots (live|staging|trial)
  nlm_config.py + profiles    # frozen RunConfig / named profiles
  nlm/
    auth.py
    cmd.py
    mapping.py                # ranks A–D policy
    source_add.py
    content_fetch.py
    notebook_lifecycle.py
    industrial.py             # thin orchestrator
  batch_status.py
  cache.py
  transcript.py
  ...

harness/                      # experiments only (may import csf) — optional post-MVP move
analyze/                      # offline only — optional post-MVP
bin/                          # thin CLIs
```

**Import rule:** `csf` must not import `harness` or `analyze`.  
Exact filenames may adjust; the **seams** and **single policy owner** are the contract.

---

## 9. Characterization suite

### 9.1 Required sets per promote (anti-theater)

| Promote | Required IDs | Notes |
|---------|--------------|--------|
| **Promote-Integrity** | **C1–C5 only** | Must encode §10.1 / §10.2. **Forbidden:** retagging existing tests that still assert order-fallback **success** (rank C/D) as “characterization” without changing asserts. |
| **Promote-Paths** | **C9** (+ C1–C5 still green if Integrity already on main) | Defaults unchanged for live; trial/staging overrides isolated |
| **Promote-Industrial-Seams** | C1–C5 + import/smoke that policy lives in extracted modules only | No dual mapping/fetch policy |
| **Promote-Experiment-Surface** | **C10** + core↛harness check | Later |
| Slice-local work | Only the C\* the slice claims | Do not require C1–C10 for every commit |

### 9.2 Cases (definitions)

| ID | Behavior under test |
|----|---------------------|
| C1 | Rank A: exact title/url/video_id extract → accept |
| C2 | Rank C/D: list-order or partial order fill → **fail closed** + mapping_failed (or successor) event |
| C3 | Duplicate source_id → fail closed |
| C4 | Status: complete never downgrades |
| C5 | Status: batch write logs and counts row failures (no silent `pass`); fail_count visible per §10.2 |
| C6 | Promote transcripts: missing/empty/same-path refused |
| C7 | Promote channel state: same fail-closed class |
| C8 | Routing: live → fallback; no_captions → NLM (unit-level) |
| C9 | Path layout: trial/staging overrides do not touch live defaults in tests |
| C10 | Config: named profile freezes batch size / source cap / cliff-related knobs |

**C6–C8:** keep if already cheap; **not** required to greenlight Promote-Integrity.

### 9.3 Markers (to add when implementing)

- `@pytest.mark.characterization` — run before every promote for the **required set**  
- `@pytest.mark.unit` — default  
- `@pytest.mark.live` — requires explicit env allow; never default  

```text
pytest -m characterization
```

---

## 10. Integrity fixes (slice 0)

Do these **before** large file moves.

### 10.1 Source↔video pairing ranks (I1) — authoritative policy

| Rank | Pairing basis | Policy |
|------|----------------|--------|
| **A** | `video_id` / watch URL / title that yields a unique 11-char id from the NotebookLM source entry | **Accept** |
| **B** | Source IDs from a **successful add** response, same length as the submitted batch, aligned to the **submitted URL/video list order for that add** (not to a later full notebook list order) | **Accept** only if add succeeded, lengths match, and IDs are non-empty/unique; covered by explicit tests |
| **C** | Zip/list-order against `source list` (or any notebook-wide list) **without** A or B identity corroboration | **Reject** — fail closed + structured event |
| **D** | Partial A/B match, then fill **remainder** by list/add order | **Reject** — fail closed + structured event |

**Implementation notes:**

- Today’s `zip(batch_ids, canonical_source_ids)` is **rank B only if** `canonical_source_ids` truly come from that batch’s successful add alignment; it is **rank C/D** if it is really notebook list order or partial fill. Implementers must not re-label C/D as B.
- Today’s order-fallback tests that expect success on partial list order are **obsolete under this policy** and must be rewritten to expect fail closed (C2), not kept green by renaming.
- Keep structured `nlm_batch_source_mapping_failed` (or successor) with counts and samples.

### 10.2 Non-silent `set_status_batch` (I2)

- On per-row exception: log `video_id` + error; increment failure counter; **never** bare `pass`.
- Return type: prefer keeping `int` ok_count **and** exposing fail_count (e.g. attribute, tuple, or structured result) so callers cannot assume “returned N ⇒ all N clean” without checking fails.
- **Caller policy (minimum):** industrial / `csf-source` paths that use batch status must surface fail_count in logs or action events when `fail_count > 0`. Do not only log inside storage and leave callers assuming full success.
- Stricter option (optional later): fail the whole batch transaction if any row fails—only if concurrency/load analysis allows; not required for Slice 0.

### 10.3 Baseline, canary, and rollback (Promote-Integrity)

**Before merge (required in evidence packet):**

1. **Baseline note (best effort):** from available logs/artifacts, record whether `source_id_order_fallback_count` / mapping_failed events appear and at what rough rate. If no artifacts: state `baseline_unavailable` explicitly—do not invent rates.  
2. **Expected symptom after I1:** possible **increase** in mapping_failed / fewer silent wrong pairs; throughput may drop.  
3. **Canary plan:** bounded staging or limited industrial run (not full VPH campaign): enough batches to see mapping_failed vs success; prefer staging DBs/notebooks when practical.  
4. **Rollback criteria (pre-declared):** e.g. after canary window, if mapping_failed rate is pathologically high relative to baseline (or vs a stated absolute cap when baseline_unavailable), **or** status fail_count storms without actionable logs → **revert Promote-Integrity** on main. Exact numeric caps may be filled at canary design time but **must not** be empty (“we’ll see”).

**After merge:** execute canary; record pass/fail; rollback if criteria hit. Canary is **mapping/status health**, not sustained VPH optimality.

---

## 11. Worktree execution plan (slices)

### 11.1 Git topology and preconditions

- **Repository:** `P:/packages/yt-is` (package-owned `.git`).  
- **Create worktree from that repo**, e.g.:  
  `git -C P:/packages/yt-is worktree add P:/.worktrees/yt-is-refactor-control-planes -b refactor/yt-is-control-planes`  
- **Do not** create this worktree from monorepo `P:\` root.  
- **Worktree root for path:** `P:/.worktrees/...` (workspace canonical). Do not use `P:/worktrees` for **new** work unless a process is already pinned there.  
- Concurrent trees under `P:/packages/yt-is/.claude/worktrees/` may exist; do not mix edits across trees for the same files without coordination.  
- Re-record base `HEAD` in amendment log or worktree README at create.  
- No live NLM/benchmark without a separate decision packet and human authorization (canary §10.3 is the only integrity-related live/staging exercise contemplated, and it is bounded).

### 11.2 Shim rule (anti-footgun — no dual policy debt)

- Temporary re-export shims are allowed **inside the worktree** during multi-commit extracts.  
- On **main** after a promote:  
  - **Allowed:** zero-logic re-export so old import paths resolve to the **single** policy implementation.  
  - **Forbidden:** two functions/modules that both implement mapping ranks, status write rules, or path policy.  
  - **Forbidden:** “leave dual policy; delete next promote” as an accepted promote state. If dual policy remains, **block promote**.

### Slice 0 — Integrity + characterization (C1–C5)
- I1 (§10.1), I2 (§10.2), baseline/canary plan (§10.3)  
- Rewrite obsolete order-fallback-success tests; add C1–C5 with **new asserts**  
- Green: required characterization for Promote-Integrity  

### Slice 1 — State plane minimum (own promote)
- `RuntimeLayout` / path helpers: live|staging|trial  
- Route existing env overrides through a **single** entry API  
- Tests: C9; default live paths behavior-identical  
- **Promote-Paths** alone—do **not** combine with nlm_batch extract  

### Slice 2 — Extract `nlm_batch` seams (behavior-preserving except already-landed I1/I2)
Order (low dependency first):

1. `mapping.py` (owns §10.1)  
2. `cmd.py` / auth command runner  
3. `source_add.py`  
4. `content_fetch.py` (including retry queue gates)  
5. `notebook_lifecycle.py` (may slip to post-MVP if needed)  
6. Thin orchestrator  

Rules: worktree re-exports OK; no new knobs; freeze/version events you move; **Promote-Industrial-Seams** only when no dual policy remains for extracted seams.

### Slice 3 — Experiment plane (post-MVP OK)
- Named profiles, run manifest, harness import boundary  

### Slice 4 — Thin CLIs (post-MVP OK)
- Peel `bin/csf-source` business logic into `csf`; CLI surface identical  

### Slice 5 — Evidence plane hardening (partial earlier)
- Fields **touched** in Slice 0/2 must keep producer tests immediately  
- Broader schema package / analyzer centralization can wait  

### Optional later
- SQLite `schema_version` migrations  
- Error enum taxonomy  
- Delete deprecated `csf_nlm_ingest` after import audit  
- Auth/browser/notebook **lifecycle reliability track** (§16.4)  
- Skill/CLI/doc matrix  

---

## 12. Commit policy (worktree)

- Prefer one seam or one integrity fix per **commit** (bisect hygiene).  
- Do not mix: “move mapping” + “change cliff timeout”.  
- Message style: `integrity:`, `refactor(nlm):`, `paths:`, `harness:`.  
- After every commit: green for **applicable** required C\*.  
- Merges follow §14 / §16; do not split promotes merely for small diffs if that leaves dual policy on main; **do** split promotes when failure domains differ (paths vs extract).

---

## 13. Risk register

| Risk | Mitigation |
|------|------------|
| Wrong transcript (uncorroborated pair) | §10.1 ranks C/D fail closed; C2 |
| Fail-closed stalls backlog (availability) | Rank B preserved; §10.3 canary/rollback |
| Silent status loss | I2 + C5 + caller visibility |
| Dual policy after partial extract | §11.2; block promote |
| Path layout + extract mixed blame | Separate Promote-Paths vs Promote-Industrial-Seams |
| Characterization theater | §9.1; no retags of obsolete success tests |
| Wrong git root / tree | §11.1 package-only worktree |
| Auth/Chrome lifecycle outages | Named track §16.4; not ignored forever |
| Live run during refactor | No VPH acceptance; canary only per §10.3 |
| Metric lies on moved events | Freeze/test fields when touched |

---

## 14. Promotion gate (worktree → main)

Human decision required. Minimum evidence packet:

1. Worktree path + branch + tip revision + **base** revision  
2. **Promote unit name** (`Promote-Integrity` \| `Promote-Paths` \| `Promote-Industrial-Seams` \| …)  
3. Commits (integrity vs pure move vs paths)  
4. Required characterization command + pass on that tip  
5. **Anti-footgun checklist** (§14.1)  
6. No live VPH used as acceptance  
7. Module map / import check when structure included  
8. Remaining debt (must not include dual policy)  
9. **If Promote-Integrity:** baseline note + canary plan + pre-declared rollback criteria (§10.3)

### 14.1 Anti-footgun checklist

- [ ] Rank C/D cannot succeed in promoted code  
- [ ] Rank A/B still possible and tested where claimed  
- [ ] No silent `pass` on status batch row failure; fail_count visible to callers  
- [ ] No second **policy** implementation of the promoted concern on main  
- [ ] Zero-logic re-exports only (if any); no dual policy debt  
- [ ] Defaults do not point trials at live DBs/browser roots (especially Promote-Paths+)  
- [ ] Live tests cannot run without explicit allow  
- [ ] Required C\* green on tip  
- [ ] Promote-Integrity: canary/rollback pre-declared  

---

## 15. Claim ledger template

| Claim | Type | Evidence | Falsifier | Action allowed |
|-------|------|----------|-----------|----------------|
| Required C\* green on rev X | measured | pytest output | any required C fail | continue / promote review |
| Pairing follows §10.1 | verified after implement | code + C1–C3 | C/D accept path remains | block promote |
| Misattribution rate in prod was high | unsupported unless measured | logs/canary | — | do not claim |
| No dual policy for concern Y | verified | grep/module map | second policy body | block promote |
| Core does not import harness | verified when claimed | grep | import edge | fix before promote |
| Ready for live VPH comparison | unsupported by default | — | — | not allowed by this charter |

---

## 16. Decisions

### 16.1 Resolved

| # | Decision | Resolution |
|---|----------|------------|
| D1 | Worktree root | **`P:/.worktrees`**, path `P:/.worktrees/yt-is-refactor-control-planes`; **git repo** = `P:/packages/yt-is` |
| D2 | Slice 0 first | **Yes** |
| D3 | Promote criterion | **Reliability (correctness + availability) and anti-footgun; not merge size** |
| D4 | Pairing policy | **§10.1 ranks A/B accept; C/D reject** |
| D5 | Promote units | **Integrity → Paths → Industrial-Seams** (separate); not Integrity then Paths+Extract combined |

### 16.2 Promote units (policy)

| Promote unit | When | Contents | Why separate |
|--------------|------|----------|--------------|
| **Promote-Integrity** | After Slice 0 green | §10.1–10.3, C1–C5 | Removes uncorroborated pairing success + silent status on the path operators run; risk reduction under incomplete evidence; canary/rollback required |
| **Promote-Paths** | After Slice 1 green | RuntimeLayout only, C9 | Path bugs are a distinct failure domain from module extract |
| **Promote-Industrial-Seams** | After Slice 2 coherent | nlm seam extract, no dual policy | Structure only when main cannot import the wrong policy body |
| **Promote-Experiment-Surface** | Later | Profiles, manifest, harness boundary | Trial non-footguns |
| **Promote-CLI-Evidence** | Later | Thin CLI; broader schemas | Operator surface |

**Rejected:**

- Paths + full extract in one promote  
- Dual policy “until next promote”  
- Live VPH as structural/integrity acceptance  
- Retagging obsolete order-fallback-success tests as characterization  

### 16.3 Still open (non-blocking for worktree create)

1. Exact profile names for Slice 3  
2. Env alias sunset schedule  
3. Whether sharded modules physically move into `harness/` in the same merge as import rules  
4. Numeric canary caps when `baseline_unavailable` (fill at canary design, before merge)  

### 16.4 Reliability track (scheduled, not Slice 0)

Auth / Chrome profile / notebook lease lifecycle is a known operational risk (registry/HANDOFF). It is **not** replaced by mapping integrity. After MVP (or in parallel if capacity allows), schedule a **lifecycle reliability** slice: preflight/postflight ownership, kill scope for lane browsers only, lease/stale rules. Do not pretend mapping fail-closed fixes auth storms.

---

## 17. Immediate next actions

1. Human accepts this amended charter (or further amends).  
2. When authorized: `git -C P:/packages/yt-is worktree add ...` per §11.1; pin base revision.  
3. Optional pre-Slice-0: log baseline for order_fallback / mapping_failed if artifacts exist.  
4. Execute Slice 0 (§10 + C1–C5 with new asserts).  
5. Promote-Integrity evidence packet + human merge decision; run canary; rollback if criteria hit.  
6. Slice 1 → Promote-Paths; then Slice 2 → Promote-Industrial-Seams toward MVP (§5.3).  

---

## 18. Amendment log

| Date | Change | Author |
|------|--------|--------|
| 2026-07-16 | Initial draft from whole-package review + gap→opportunity map | agent draft |
| 2026-07-16 | Reliability/anti-footgun promote policy; D1–D3 | agent amend (human direction) |
| 2026-07-16 | P0/P1 critique fixes: §0 execution card; pairing ranks A–D; canary/rollback; Promote-Paths vs Industrial-Seams; characterization anti-theater; package git topology; hard shim rule; MVP end-state; G9 availability; epistemic downgrade on misattribution claims; lifecycle track | agent amend (human: apply critical review) |

---

**End of charter.**  
Authorizes planning and worktree structural work after human approval. Does **not** authorize live VPH campaigns, unattended promotion to main, or dual-policy debt on main.
