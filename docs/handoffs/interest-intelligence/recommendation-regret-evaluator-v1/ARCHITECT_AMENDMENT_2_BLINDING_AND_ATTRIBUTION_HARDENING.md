---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_453bc40e-9bbd-4d62-b7da-5cbc2c9f655a
amendment: ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING
supersedes: amendment-1 candidate (landed 4d492a471, REJECTED by fresh review of reviewer sess_a6d384cd-42eb-4b2a-8e71-ba10c7c6b6c0)
status: AMENDMENT_2_READY_FOR_FRESH_REVIEW
---

# ARCHITECT AMENDMENT 2 — BLINDING AND ATTRIBUTION HARDENING (RRE v1)

Authority: the architect accepted the fresh amended-candidate review's
REJECTED verdict (reviewer session
sess_a6d384cd-42eb-4b2a-8e71-ba10c7c6b6c0; verdict received via the
architect packet) and directed this amendment. The rejected
amendment-1 candidate remains immutable history at commits 4d492a471
(candidate) and 3656e96f6 (integration) on main. No private
Recommendation labels were used; no real ranking outputs were
inspected; no real judge packets were run; synthetic/adversarial
fixtures only.

## R3 — structural blinding vocabulary: normalized key classification

The v2 validator no longer relies on blacklist growth over literal
key names. Every KEY anywhere in a judge-facing document is classified
after NORMALIZATION:

1. Unicode NFKC;
2. strip zero-width/invisible characters (U+200B–U+200F, U+2060–U+2064,
   U+FEFF, soft hyphen);
3. fold separators (whitespace, hyphen, underscore, dot) away;
4. casefold.

A normalized key is machinery if it is exactly equivalent to a
machinery class (arm, armid, treatment, variant, condition, bucket,
policy, policyname, policyversion, rankingpolicy,
rankingpolicyversion, score, rawscore, rawrankingscore, rank,
rankposition, experiment, experimentid, propensity,
similaritydiagnostic, sidecar, blindsalt, blindslotmap,
blindedpackethash, mapping, blindmap) OR contains a machinery
substring (score, rank, propensity, policy, experiment, sidecar, arm,
treatment, variant, condition, bucket, diagnostic, blind, mapping) —
unless it is a sanctioned structural key (blindedpacket, packetschema,
packetid, markersynthetic, recentsurfaceditems, itemid, title,
claims, whysurfaced, evidencerefs, relatedrecords, questions,
question, instruction, kind, id, lists, k).

This is defense-in-depth. The PRIMARY protection is positive:

- judge item views may contain ONLY item_id, title, claims,
  why_surfaced, evidence_refs, related_records — enforced on pairwise
  items AND listwise list entries;
- related_records entries match the positive schema {"kind", "id"}
  only — unknown structured keys fail closed at build time AND at
  validation;
- controlled type errors (non-string prose fields, non-array refs,
  non-object views/entries, scalar related_records) are validation
  errors, never tracebacks.

Regression-tested: arm / ARM / Arm / arm-id / arm.id / arm<space>id /
fullwidth ａｒｍ / zero-width-padded / small-capital ᴀʀᴍ /
variation-selector (U+FE04, U+FE0F) and MVS (U+180E) padded variants /
treatment / variant / condition / bucket / policy / score / rank /
experiment / propensity / similarity_diagnostic / sidecar / raw_score
— at item top level and nested inside structured containers. Residual
(documented): exotic homoglyph key spellings beyond the bounded fold
table (e.g. small-capital ʙrm, which normalizes to "brm" and is not an
arm-equivalent spelling), authored into CURATOR-supplied wrapper keys,
are a curator-input integrity concern — inside judge-visible
containers the allowlist + positive schema catch any unknown key
regardless of spelling.

## Free-text channel boundary (F3, part 1)

Structural scanning applies to KEY NAMES, never to natural-language
VALUES: title, claims, why_surfaced, and evidence_refs are
curator-transcribed free text/refs. Ordinary English words ("arm",
"rank") in prose are legitimate content and are NOT banned. The
preregistration's blinding claim is NARROWED to the mechanically
enforced surface (below); content-level adversarial prompt injection
through curator free text is documented as a SEPARATE evaluator threat,
out of scope for structural blinding.

Curator rules (documented, procedurally binding on the later
independent curator): judge-visible prose must describe the item, not
the pipeline — no policy names, arm labels, ranks, or scores in
title/claims/why_surfaced/evidence_refs.

## Evaluator-owned opaque IDs

Producer-controlled IDs no longer render to the judge:

- item_id: replaced in the judge view by an opaque token
  RRE_J_<hash16> derived per-packet (frozen salt | packet_id | "j" |
  real id) — never from the arm, so the same real item yields the same
  token in both arms and duplicate-across-arms detection survives
  (regression-tested);
- recent_surfaced_items: replaced by packet-scoped opaque tokens
  RRE_R_<hash16> (regression-tested);
- related_records ids: replaced by RRE_REF_<hash16> (regression-tested).

The sidecar (sealed, out-of-band, curator-only) carries the
judge-id -> real-id map under item_ids. evidence_refs deliberately
remain curator-supplied strings: they must resolve against the
curator's evidence inventory for provenance, and are covered by the
curator rules above.

## R5 — sidecar map authenticated by re-derivation AND keyed MAC

The stored reverse_map is NEVER trusted. At unbind (pairwise AND
listwise, same code path):

1. binding_version must be current (rre_v3_sidecar_binding_3);
2. declared arms must be exactly two distinct non-empty arm id strings
   (no fantasy arms, no three-arm maps, no duplicates; non-string
   elements are controlled errors, never tracebacks);
3. the expected slot assignment is RE-DERIVED via canonical_binding()
   — the same frozen assignment function + frozen salt used at build
   time — over the packet identity and the declared arm set;
4. supplied slots AND reverse_map must EQUAL the re-derivation exactly
   (no collisions, no duplicate mappings, no flips);
5. the packet+arms+map digest must reproduce over the actual packet
   bytes;
6. the sidecar's HMAC-SHA256 ("binding_mac") must verify under the
   blind-time BINDING SECRET: `blind` generates a 256-bit secret,
   writes it out of band (CLI --secret-out, required, refused inline
   beside the packet or sidecar), and every unbind requires it.

The MAC covers packet + arm set + slot map + binding version, closing
the landing-review round-3 blocker: a forger who renames arms and
re-derives the map with the public salt still cannot produce a valid
sidecar without the secret. Regression-tested with the exact forgery
(rename A/B to W/Z, re-derive, recompute digest → rejected on MAC),
the flip shape, wrong/wrong-secret/missing-secret cases, and the
demonstrated 5/5 baseline-sweep inversion — all fail closed at unbind.

Secret hygiene is part of the protocol: the binding secret travels
out of band, beside neither the packet nor the sidecar (same trust
class as sidecar sealing). Losing it renders the packet set
ununbindable (fail closed); a leaked secret invalidates the run.

## F3 — blinding claim scope narrowed

METRIC_PLAN_PREREGISTRATION.md AMENDMENT 2 now states the claim as
exactly the mechanically enforced surface: structural blinding
(allowlist-built payloads, normalized machinery-key classification,
positive schemas, evaluator-owned opaque identity tokens,
re-derivation-authenticated sidecars) over the fields the code
actually controls; curator-attested free text under documented curator
rules; content-level prompt injection explicitly out of scope. No
broader claim than the implementation proves.

## Cheap hygiene fixes (reviewer minors)

- packet_id required and validated at build (build + listwise) and at
  validation (regression-tested).
- wrong-typed items/views/evidence_refs/related_records (including
  scalar related_records and non-object views) produce controlled
  validation errors, never tracebacks (regression-tested).
- unhashable sidecar arms/slots values (e.g. a list inside slots) are
  controlled PacketBindErrors, never TypeErrors (regression-tested).
- a missing judgment raises PacketBindError — it can never silently
  become NEITHER (regression-tested, pairwise).
- a missing or unknown preferred_list raises in listwise aggregation
  too — the listwise silent-tampering path found in landing-review
  round 3 is closed (regression-tested).
- an unknown preferred_arm (not TIE/NEITHER/either designated arm)
  raises PacketBindError (regression-tested).
- non-string opaque-ID sources (item ids, recent ids, relation ids)
  fail closed instead of being silently coerced (regression-tested).
- dimension ratings are explicitly described in every report as
  DIAGNOSTIC-ONLY: collected per packet, never aggregated (no
  aggregation function exists or is planned without amendment).
- listwise sidecars now carry the judge-id -> real-id map too
  (item_ids), matching the pairwise sidecar.
- amendment-1 hash-table discrepancy resolved (see below), and the
  amendment-2 document IS a frozen artifact: FROZEN_ARTIFACT_PATHS and
  the regenerated receipt include it; the receipt self-labels
  rre_v1_freeze_amendment_2 and points amendment_doc at this file.

## Landing-review round-3 dispositions (run-e499c8f98dd9, REJECT)

- F1 BLOCKER (relabel forgery of the unkeyed digest) → FIXED: keyed
  HMAC binding (R5 section above); exact forgery regression-tested.
- F2 MAJOR (amendment-2 doc not in receipt/artifact list) → FIXED:
  FROZEN_ARTIFACT_PATHS + receipt labels updated.
- F3 MAJOR (listwise missing/unknown preferred_list silently
  uncounted) → FIXED: raises PacketBindError.
- F4 scalar related_records TypeError → FIXED (controlled error).
- F5 unhashable arms/slots TypeError → FIXED (controlled error).
- F6 small-capital / variation-selector / MVS key spellings → FIXED:
  invisible set extended (VS1-16, U+180E, etc.), bounded small-capital
  fold table added; exotic homoglyphs documented as residual.
- F7 listwise sidecars lacked item_ids → FIXED.
- F8 hash-table parenthetical clarified (below) + opaque-id coercion
  removed → FIXED.

## Preserved unchanged

Personal regret grounding (LLM judge = frozen labeled instrument, not
operator ground truth); off-policy contract (descriptive-only
unknown-propensity feedback, exclusions override, explicit roles, no
estimator, OffPolicyError seam); question orthogonality with
TIE/NEITHER and no forced choice; nine dimensions with no aggregation;
finite-set outcome exactness; diagnostic floor semantics; evidence
classes required on all reports; frozen judge prompts (byte-identical
across v1/amendment-1/amendment-2 — verified programmatically).

## Hash chain (amendment 2)

| Artifact | sha256 |
|---|---|
| amendment-1 module (rejected, historical) | f98deec4556529b06e0c3ed53ae202d8fc7bf804548c80ef3b0de9e4c2681c92 (landed 4d492a471; the ORIGINAL v1 bytes are preserved under rejected-candidate-v1/) |
| ef/eval_recommendation_regret.py (amendment 2 rev 2) | 195ab347f91ad6409ef0b6b5e33f4e4a0d23a34c055657f033e3b0cab1f90cb2 |
| scripts/eval_recommendation_regret.py (amendment 2 rev 2) | ff9a24bc3b8a932ed2e7f1206c5a8661910c474cfa16ceb2a03ed1566709e4fd |
| tests/test_eval_recommendation_regret.py (amendment 2 rev 2) | 2b7f5f7aefe6508db0b08249a3244ae3cae82daee360b613bf639170b7a36307 |
| METRIC_PLAN_PREREGISTRATION.md | hashed in the regenerated FREEZE_RECEIPT.json |
| ARCHITECT_AMENDMENT_1_REVIEW_REPAIR.md | hashed in the regenerated FREEZE_RECEIPT.json (retained as history; its candidate-hash table rows describe amendment-1 bytes) |
| ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING.md (this file) | hashed in the regenerated FREEZE_RECEIPT.json |

Note on the amendment-1 hash-table discrepancy the reviewer flagged:
the landed amendment-1 module hash is f98deec4… (the receipt re-pin
commit d22d6e8795 recorded it); the 2599546… row in the amendment-1
document described the pre-staging bytes. rejected-candidate-v1/
preserves the ORIGINAL v1 bytes only; the rejected amendment-1
candidate lives in git history (4d492a471/3656e96f6). This table is
regenerated against the exact amendment-2 rev-2 bytes at freeze time
and verified by verify-freeze.

## Landing-review round-4 dispositions (run-1fcfced30c6a, REJECT)

- MAJOR (stale hash table pinning pre-repair bytes) → FIXED: table
  above regenerated against the final rev-2 bytes and receipt.
- MAJOR (F6 disposition overstated: VS6-15 bypass; claimed tests
  absent) → FIXED: invisible set now strips the FULL variation
  selector block U+FE00–U+FE0F plus U+180E; the parametrized
  regression tests for small-capital ᴀʀᴍ, VS-padded (U+FE04, U+FE0F),
  and MVS-padded variants now EXIST in the suite; the ʙrm example
  corrected (normalizes to "brm", not an arm-equivalent; documented
  residual).
- MINOR (non-ASCII binding_mac TypeError from compare_digest) →
  FIXED: MAC comparison guards to ASCII hex, else controlled
  PacketBindError (regression-tested).
- MINOR (test-count inconsistency 109 vs 111) → FIXED: 116 tests
  (verified in both doc mentions and against the suite).

## Tests

116 offline tests (73 before amendment 2), including the
amendment-2 adversarial set: normalized machinery-key variants
(arm/ARM/Arm/arm-id/arm.id/space/fullwidth/zero-width/small-capital
ᴀʀᴍ/variation-selector U+FE04 and U+FE0F/MVS U+180E),
treatment/variant/condition/bucket, nested structured leakage, wrong
sidecar reverse_map, same-layout flipped reverse_map, fantasy arms,
colliding/duplicate/non-string slots, three-arm maps, pairwise and
listwise re-derivation, missing packet_id, malformed field types,
missing judgment, junk preferred_arm/unknown preferred_list, opaque-ID
properties, related_records positive schema (build + validate),
dimensions diagnostic-only note, binding-secret
requirement/wrong-secret/short secret/non-ASCII-MAC guard, the
relabel-forgery MAC rejection, and the reviewer's demonstrated
baseline-sweep inversion falsifier.

## Stop condition

Implemented, tested (116 passed), frozen (receipt regenerated,
verify-freeze OK), and published on the implementer lane branch.
Per the architect decision: STOP — a fresh reviewer reviews
amendment 2; no self-review was performed.
