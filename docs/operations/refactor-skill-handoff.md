# Handoff — `/refactor` skill hardening (cold-start)

**Audience:** A fresh LLM session with no chat history. Read this top-to-bottom; every claim cites a source on disk.

**Goal (one sentence):** Make the Grok `/refactor` skill produce **fewer hallucinations and more quality useful outcomes** (not fewer lines — line count is explicitly NOT the optimization target).

**Status:** Design settled via critical review + empirical test. Not yet built. Next step is the deterministic floor (mechanisms 1-2-6 below), then mmx as a *second* check, not the sole gate.

---

## 1. Where things live

| Artifact | Path | Role |
|----------|------|------|
| Skill under development | `P:\.grok\skills\refactor\SKILL.md` | The `/refactor` orchestrator (plain-language, plan/execute, seams, worktree cleanup) |
| `/go` routing | `~/.grok/skills/go/SKILL.md` | Routes refactor-shaped work to the skill |
| Last run artifact | `P:\tmp\grok-refactor\yt-is\20260717-071452\{PLAN.md,seams.json,_run.json}` | Dry-run plan for yt-is; do NOT trust seam content as verified (see §4) |
| Blind verifier test | `P:\tmp\grok-refactor\yt-is\20260717-071452\_blind_a2_msgs.json` + M3 output (in terminal log) | Proof that cross-model blind check catches misframes |
| Red-team plugin (reference) | `P:\packages\.claude-marketplace\plugins\red-team\` | Source of schema + claim-refuter patterns |
| Claude refactor (reference) | `P:\packages\.claude-marketplace\plugins\cc-skills-sdlc\skills\refactor\SKILL.md` | Source of fail-closed discovery tools + RED gate |
| This file | `P:\packages\yt-is\docs\operations\refactor-skill-handoff.md` | Cold-start handoff |

---

## 2. The two failure modes we are fixing (evidence)

From a live `/refactor yt-is` run + red-team of its own output:

- **Hallucinated seam (B2):** Plan cited `csf_nlm_import.py`, `channel_identity.py`, `scripts/import_video_ids.py` as carrying a "Promote REPLACE / claim-unclaim race." Grep proved **0 matches** in those files. The seam was invented from prior-session memory, not a read.
  - Falsifier: `rg "promote|REPLACE|claim|unclaim" csf/csf_nlm_import.py csf/channel_identity.py scripts/import_video_ids.py` → empty.
- **Misframed seam (A2):** Plan called the canonical bind at `nlm_batch.py:3042` ("uncorroborated"). Reality: `_last_added_source_ids` is set from add-stdout parse (`nlm_batch.py:2432`), so the positional correlation is **corroborated**. The *real* uncorroborated path is the fallback loop at `nlm_batch.py:3059-3063` (leftover source_ids zipped by list order).
  - Falsifier: `_last_added_source_ids` definition `:1894`, set by parsed stdout `:2432`, summed `:2852`/`:2915`.

**Read these two before touching the skill.** They define what "hallucination" and "misframe" mean here.

---

## 3. The mechanism set (consensus from 3 sources)

Sources converged: (a) local red-team + claude-refactor teardown, (b) web research (MARCH/CoVe/blind-reviewer), (c) live mmx/M3 test. All three point at the same 5 mechanisms.

| # | Mechanism | Type | Catches | Build order |
|---|-----------|------|---------|-------------|
| 1 | **Seam schema + validator** (`location, evidence, severity, claim_type` required; reject otherwise) | mechanical | hallucinated seams | FIRST |
| 2 | **`claim_type: scope-completeness` → repo-wide grep**, not author's named file | mechanical (`rg` avail, v14.1.0) | wrong-file / wrong-scope claims | FIRST |
| 3 | **Blind verifier via `mmx --model minimax-m3`** for P0 seams (code-only prompt, structured verdict) | probabilistic, cross-model | interpretive misframes (A2-shape) | SECOND (as 2nd check) |
| 4 | **3-4 scoped discovery agents** (correctness, structure, scope), schema-bound, fail-closed tools (`Read,Grep,Glob,Write` no Bash), fresh context each | coverage | single-author blind spots | THIRD |
| 5 | **Structure close-gate (mechanical)** — grep proves old path gone / single writer; not a prose quality judgment | mechanical | "done" without real improvement | with #4 |
| 6 | **RED-phase gate** — characterization test verified failing before structure change | deterministic | behavior drift | FIRST (already in skill; enforce) |

**Empirical proof for #3:** blind M3 run on A2 code (no author framing) returned `uncorroborated_label_accurate_for=` the fallback loop at `3059-3063` — independently matching the red-team's A2 correction, plus a nuance (canonical zip overrides before failure check). Verdict line: `defect_at=none and uncorroborated_label_accurate_for=<fallback loop>`.

---

## 4. Critical-review corrections (do NOT skip these)

The proposal was itself critiqued. Hard constraints before building:

1. **mmx is NOT the sole P0 gate.** It is an LLM and can be wrong. Deterministic floor = schema (1) + quote-grep (2) + RED test (6). mmx is the *second* check, only on **ambiguous P0 seams** (`evidence_kind: tool_read_partial` or contested interpretation). Never let one probabilistic call be the only VERIFIED label.
2. **Structure close-gate must be mechanical or dropped.** "Coupling reduced" has no tool — report `net_loc` as honesty, don't gate on it. Gate only on deterministic greps: old path no longer called; one writer module for the concern.
3. **Discovery agents must be independent readers, not echoes.** Each reads source itself (fresh context, disk-backed handoff à la red-team `commands/red-team.md:110-119`); they must not receive the orchestrator's interpretation. Otherwise fan-out multiplies the same blind spot.
4. **Health = one ratio, not a score.** Use `seams_closed_with_dual_path_collapsed / seams_closed`. Drop generic "Health Score" framing (the Claude plugin's score needs a corpus; we have none).
5. **Correctness findings are their own P0 seams**, not a gate that blocks all structure work. The walk already does integrity first.

---

## 5. Build order (incremental, falsifiable at each step)

**Wave 1 — deterministic floor (no mmx dependency):**
- Add seam schema + validator script (port `red-team/__lib/findings_schema.py` shape: required `location, evidence, severity, claim_type`; reject missing).
- Add `claim_type` enum; route `scope-completeness` to an `rg` across the package, not the named file.
- Enforce RED-phase gate as a hard block in execute.
- *Falsifier:* re-run `/refactor yt-is`. B2-shape (symbol not in cited file) must be rejected structurally. (This half is deterministic and must pass.)

**Wave 2 — blind second check:**
- Wire `mmx --model minimax-m3` as a P0-only second check on ambiguous seams. Prompt = code excerpt + "locate defect / is label accurate", no author outcome. Parse structured verdict.
- *Falsifier:* A2-shape must be caught blind (already proven once; re-confirm on re-run).

**Wave 3 — useful outcomes:**
- 3-4 scoped discovery agents (correctness, structure, scope-completeness), schema-bound, fail-closed tools, fresh context.
- Mechanical structure close-gate (grep old path / single writer).
- Health ratio in RESULT.md.

**Final falsifier (all waves):** re-run `/refactor yt-is`. Pass iff: (a) zero hallucinated seams, (b) zero misframed seams, (c) discovery agents surface ≥1 real issue the single-author pass missed. The third condition is what "useful outcomes" means.

---

## 6. Constraints from the user (non-negotiable)

- **Optimization target = fewer hallucinations + more useful outcomes.** NOT line count. Do not trim mechanisms to save skill LOC.
- **Plain-language, flags-optional** interface (matches `/review`, `/go`): `/refactor yt-is` → plan; named defect → slice; "implement findings" → budgeted walk.
- **Worktree + cleanup decision** required whenever a worktree is used (multi-terminal `P:\` workspace).
- **No 12-agent fleet, no phase-ledger state machine, no Health Score without corpus, no constitutional filters.** Thin orchestrator (fan-out + collect + sort); agents are schema-in/schema-out.

---

## 7. Open questions (answer before Wave 3)

- How many discovery agents exactly (3 vs 4), and which lenses? Recommend: correctness, structure, scope-completeness.
- Where does mmx prompt live — inline in SKILL.md or a `__lib/` script? Recommend `__lib/blind_verify.py` (reusable, testable).
- Does the validator script live in `~/.grok/skills/refactor/__lib/` or `P:\.grok\skills\refactor\__lib\`? Match skill's own dir.

---

## 8. One-line resume prompt for a cold LLM

> "Continue hardening the Grok `/refactor` skill. Read `P:\packages\yt-is\docs\operations\refactor-skill-handoff.md` first. Goal: fewer hallucinations + more useful outcomes (not fewer lines). Build Wave 1 (deterministic floor: seam schema+validator, claim_type scope-grep, RED gate) next, then falsify by re-running `/refactor yt-is`."
