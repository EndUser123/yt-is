---
agent: zcode
host: zcode
created: 2026-08-26
session: sess_ab0a0135-9c07-432c-af42-c9305e09195e
status: COMPLETE
packet: ADDITIVE REQUIREMENT — stale artifact cleanup + fixed-path hazard removal
---

# Inference artifact hygiene — stale result deletion and run-scoped artifacts

## Stale inference artifact

    path:               P:/tmp/interest-inference-result.json
    identity verified:  YES (metadata + structural only, per order)
      - sha256 cbd756a7165f3d97e1bdc31263e89f9d1e555ca31f6b60f9417264dd4e70e994
      - size 16,463 bytes; mtime 2026-08-26T13:15:46 local (hours before
        this session's provider activity; no run of this session ever
        wrote that path)
      - top-level structure: inferred_interests / questions /
        regret_candidates; counts 12 / 6 / 4 — matches the diagnosed
        "approximately 12 interests + 6 questions"
      - ownership scan: no live process writing or holding it
    deleted:            YES (confirmed absent post-delete)
    reason:             architect ruling — PROVIDER_OUTPUT_EXISTS only;
                        never accepted through any valid Interest gate;
                        never persisted to canonical state; must not
                        serve as accepted personal state.
    Semantic contents were not inspected beyond counts and were not
    copied anywhere.

Canonical semantic state changed: NO (verified: typed tables untouched
by this lane; the file was never an input to any persistence path).

## Fixed global result path removed

`P:/tmp/interest-inference-result.json` is eliminated from the supported
inference path (`scripts/build_interest_graph.py`):

- `run_inference` defaults now allocate a unique
  `ARTIFACT_ROOT/runs/<run_id>/` (run_id = timestamp + random hex +
  kind) holding prompt.txt and — only after successful validation —
  `result.validated.json`, whose envelope stamps run_id,
  validation_status="validated", payload, and result_hash.
- `run_bootstrap` run directories now embed a random suffix, so even
  same-second retries get distinct homes; every batch prompt lives under
  `<run_dir>/prompts/<batch_id>-prompt.txt`.
- `run_reconciliation_tree` without an explicit prompt path allocates a
  fresh per-tree run directory for its group prompts — removing both
  cross-run and intra-run overwrites that the old shared name allowed.
- `run_batch_inference` direct-call default also moved under runs/.
- Explicit path overrides (tests/driver) remain supported; callers who
  pass exact paths own their uniqueness, as before.

The three-state distinction is preserved mechanically: a completed
provider payload alone never lands on disk as state, validated output is
stamped but explicitly non-canonical, and canonical persistence still
requires explicit store=True through the transactional store path gated
on full validation + reconciliation.

Concurrent-run isolation tests (new `tests/test_artifact_run_isolation.py`,
9 cases mapping to the packet's enumerated requirements):

1. two concurrent runs receive different paths — `_new_run_dir`
   uniqueness incl. same-second identity space.
2. one run cannot overwrite another — bootstrap reruns produce disjoint
   directories, each owning its prompts/summary.
3. failed validation leaves no pointer — legacy run dir holds ONLY
   prompt.txt after a contract failure.
4. failed reconciliation fails the bootstrap closed leaving no
   final-validated-result.json anywhere in the run tree (existing suite
   already pins fragment-stage fail-closed).
5. stale prior-run artifacts are never selected implicitly — read-spy
   over the whole legacy execution observes zero reads of prior-run or
   retired paths.
6. persistence requires explicit validated payload + run identity —
   provenance fields asserted end-to-end from validation to meta.
7. retry freshness — distinct roots across repeated executions.
8. banned literal absent from every supported source file, and the
   retired P:/tmp file verified still absent at test time.

Regression updates: the pre-existing legacy-artifact test now asserts the
validation envelope instead of the old bare-payload dump (behavior change
is the point of this packet).

Scope guardrails kept: Interest semantics, prompts' semantic content,
ground truth, recall thresholds, Recommendation, Discovery — unchanged.
Artifacts of the completed bakeoff were untouched.
