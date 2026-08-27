# E3.1 PREREGISTRATION — Generative Label Operational Viability

Agent: zcode (continue-existing-implementer sess_bc0b8ab7). Architect
directive governs scope: E3's NO_MATERIAL_DIFFERENCE verdict is NOT
rewritten; this measures the two operational blockers only, plus a
portability diagnostic and a stale-label lifecycle study. Production
labels remain unchanged regardless of outcome.

Frozen quality evidence (E3, not re-measured): C-arm referent 4.50,
clarity 4.71, artifact rate 2.2%, forced-choice 43/45 on n=45.

## Gates frozen BEFORE measurement

Memory accounting (Blocker 1) — metrics collected separately:
  M1 process peak RSS of the labeler process in LABELER-ONLY mode
     (no bge-m3 weights loaded in-process; membership/titles/keywords +
     C labeling need only Qdrant vectors by point_id, c-TF-IDF via
     sklearn, and proxy calls);
  M2 incremental RSS attributable to label generation (peak minus
     interpreter/cold-start baseline);
  M3 shared resident model/service memory identified but NOT charged to
     the job (bge-m3 resident under ef embedding infrastructure);
  M4 whole-system committed-memory delta while the job runs;
  M5 steady-state after job exit.
  ACCEPTANCE: M1 <= 1024 MB AND M2 <= 512 MB AND M5 returns within 10%
  of pre-job baseline. E3's old 4GB gate stands unweakened as a
  historical record; acceptance here uses these operational-mode gates.

Provider resilience (Blocker 2) requirements implemented and verified:
deterministic queue (ascending cluster_id), per-cluster request hash =
sha256(model|prompt|config), immutable result cache keyed by that hash,
resume-safe (no repeat calls for valid cached results), bounded retry
(<=6 attempts, exponential backoff, 429/quota classified QUOTA vs other
failures SEMANTIC/TRANSPORT), per-row receipt {model, route, config,
prompt_hash, latency, attempt count}. Already-produced labels are never
regenerated silently: t0 C labels stay byte-frozen; only pert-C rows,
the k=3 repeats, and new-provider rows are produced fresh.

Portability diagnostic: second provider = Nemotron 3.5 Lightning
(`codex-opencode-zen-nemotron-3-5-lightning-free`) if its route answers;
frozen identical prompt/mechanism (build_prompt unchanged) over the SAME
45-sample t0 inputs (display titles + c-TF-IDF top-20). NEW blinded
reviewer seat (fresh session, never seen packets or keys) compares for
each sampled cluster: A0 stored label + Hy3-generated label +
Lightning-generated label, anonymized W/X/Y/Z randomized; same rubric/
axes/flags as E3 INSTRUCTIONS (referent/specificity/clarity/granularity/
artifact_free 1-5, TOO_NARROW etc flags, OVERALL_PREFERRED).
PORTABILITY ACCEPTANCE: adjudicated mean REFERENT_FIDELITY(Lightning)
>= REFERENT_FIDELITY(Hy3) - 0.35 AND Lightning ARTIFACT flag rate <=
4x Hy3's 2.2% (=8.8%) — i.e., mechanism-level quality, not a model
one-off. If no second provider route can answer >=40/45 clusters after
all retries, portability outcome = NOT_TESTED (does not block
OPERATIONAL_* decisions on its own; recorded in enum mapping).

Decision mapping (frozen, first match):
1. Pert coverage complete (185 missing pert-C done), repeats complete
   (k=3 x 45 done), resumability demonstrated (>=1 real interruption or
   injected resume cycle exercised), memory gates met, portability met
   → GENERATIVE_LABEL_OPERATIONALLY_SUPPORTED
2. Portability failed while ops passed → GENERATIVE_LABEL_QUALITY_NOT_PORTABLE
3. Ops gates unmet after provider exhaustion → OPERATIONAL_BURDEN_STILL_BLOCKING
4. Provider never answers enough to measure → INSUFFICIENT_OPERATIONAL_EVIDENCE

Production promotion: none in any branch.

## Stale-label lifecycle (study + design only)

Quantified read-only from live catalog (allowed like E3's D2 baseline
pull): per-cluster updated_at vs max(chunk assigned_at); distribution;
label-time vs cluster-time drift; identity-survival analysis from
clustering.py code path (full DELETE+INSERT → identity not guaranteed
across reclusters). Design artifact (no deployment): representation
bound to membership-version hash; STALE marking; regeneration hook.
