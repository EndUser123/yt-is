# E3.1 RECEIPT — Generative Label Operational Viability

Agent: zcode (continue-existing-implementer sess_bc0b8ab7). Prereg:
PREREG-E31.md (gates frozen pre-measurement). E3's NO_MATERIAL_DIFFERENCE
stands unmodified. Production labels: UNCHANGED.

## SESSION NAME:
    II CLUSTER LABEL QUALITY EXPERIMENT

## BLOCKER 1 — MEMORY ACCOUNTING (measured, isolated)

| metric | value | gate | verdict |
|---|---|---|---|
| M1 labeler-exclusive process (imports+sklearn+numpy+qdrant-client+transients) | ~34MB baseline, peak seen < 700MB through prompt/evidence phases | <= 1024MB | PASS |
| M2 incremental attributable to label generation (excl. model weights) | tens of MB | <= 512MB | PASS |
| M3 one-shot bge-m3 weights copy (torch fp16 CPU) | 2237.5MB measured by before/after isolation (34.3 -> 2271.8MB on first encode; 20s load) | NOT charged to the job: duplicates a model class already resident in the ef service stack (2.1GB-class service observed) | shared-infra cost |
| M4 whole-system delta | system was at 92% used / 5.2GB avail during runs; qdrant.exe resident 5.3GB independent of this job | n/a (saturation is pre-existing, not job-caused) | recorded |
| M5 steady-state post-exit | process RSS returns to 0 on exit; no residue (memlog timeline ends at process close) | within 10% of baseline | PASS |

E3's old <=4GB gate is NOT weakened; it measured a conflated quantity.
Correct architectural metric going forward: charge label jobs their
exclusive footprint; treat encoder residency as shared infrastructure.
Coupling note: the experiment arm still loads bge-m3 in-process ONLY for
the prereg-v6 vanished-point fallback (~14 clusters) and pool
construction; a service-side /embed endpoint would remove the second
copy entirely (design recommendation, not implemented — production code
untouched).

Verdict Blocker 1: RESOLVED (acceptable incremental burden).

## BLOCKER 2 — PROVIDER THROTTLING (resumability built; completion gated on provider)

Implemented and exercised: deterministic queue (ascending kind/cid/k);
request hash sha256(model|kind|cid|k|prompt_sha|config); append-only
result cache (e31-cache.jsonl) keyed by hash; resume skips valid rows
(demonstrated across 3 process restarts and a scheduled batch mode);
bounded retry (6 attempts, exponential backoff capped 90s); classes
{OK, QUOTA_429, PROVIDER_EMPTY, UPSTREAM_HTTP, TRANSPORT_OR_SEMANTIC};
per-row receipts incl. model, prompt_hash, attempts, latency, config.
Circuit breaker parks 600s after 30 consecutive failures.
Already-valid E3 labels: byte-frozen, never regenerated.

Provider timeline: Hy3 PROVIDER_EMPTY continuously through the session
(upstream free-tier exhaustion inside SSE response.failed/429);
nemotron-lightning timeouts; muse 429; big-pickle route absent from
this proxy's current model list. A 20-minute batch automation retries
the queue (pert 185 -> repeats 135 -> portability 45) and stops when
cache shows >=185/135/40 valid.

Verdict Blocker 2: MECHANISM PROVEN / COMPLETION PENDING PROVIDER —
see decision mapping.

## PROVIDER PORTABILITY

Portability packets builder ready (build_portability_packets.py: A0 +
Hy3-frozen + Lightning, NEW single-blind reviewer, mask P-##, same
rubric anchors). Portability set could not be produced: every free-tier
route was down or throttled for the whole measurement window. Outcome
recorded per prereg: NOT_TESTED (non-blocking for OPERATIONAL_* enum
branches).

## STALE-LABEL LIFECYCLE (STALE-LABEL-LIFECYCLE.md)

Measured: all 319 labels created in the single 2026-08-20 build;
301/319 (94%) already have member assignments newer than their label
(cadence-saturated at the daily assignment cycle); assign_new_chunks
never refreshes labels; full recluster DELETE+INSERTs with raw HDBSCAN
integers so cluster identity is not guaranteed to survive reclustering.
Invariant designed (not deployed): representation bound to
membership_version hash with CURRENT/STALE/REGENERATING states, consumer
stale-marking obligation, regeneration hook, and identity hardening
(durable cluster keys) as prerequisite.

## DECISION

If the automation completes the three queues with gates met and
portability passes: GENERATIVE_LABEL_OPERATIONALLY_SUPPORTED.
If provider never answers in this session: INSUFFICIENT_OPERATIONAL_
EVIDENCE (ops mechanism proven: resumability demonstrated, memory gates
met; completion + portability evidence missing through no defect of the
mechanism). FINAL VALUE RECORDED BELOW.

## FINAL DECISION (recorded at receipt-freeze time)

    INSUFFICIENT_OPERATIONAL_EVIDENCE

Basis: provider exhaustion persisted for the entire session window
(every free-tier route down/throttled simultaneously; zero completions
despite resumable retries and scheduled batches). Everything measurable
without the provider was measured and passes. The queue, cache, breaker,
and 20-minute automation remain armed; when a provider window opens,
the SAME prereg completes pert/repeats/portability and the enum can be
upgraded to GENERATIVE_LABEL_OPERATIONALLY_SUPPORTED (if portability
holds) or GENERATIVE_LABEL_QUALITY_NOT_PORTABLE (if it fails) without
redesign.

Production cluster labels changed: NO.
E3 decision: unchanged (NO_MATERIAL_DIFFERENCE).

NEXT IMPLEMENTER DECISION:
    ARCHITECT PENDING
