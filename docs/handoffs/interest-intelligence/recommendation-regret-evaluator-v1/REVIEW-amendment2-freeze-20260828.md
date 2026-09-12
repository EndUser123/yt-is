---
agent: zcode
host: zcode
created: 2026-08-28
reviewer_session: sess_541bbb5b-84ff-46ff-a219-bc3ab6423fe0
target_candidate: 01eed828c2983ea9edb7ffda73d0eb8c55a9946b
target_integration: c174762683521ee038f8a7c4811eccaec2692d75
amendment: ARCHITECT_AMENDMENT_2_BLINDING_AND_ATTRIBUTION_HARDENING
verdict: REJECTED
contamination: "no private labels accessed; no real ranking outputs inspected; synthetic/adversarial fixtures only; author tests re-executed plus independent probes"
---

# RRE v1 Amendment-2 fresh freeze review — REJECTED (2026-08-28)

Fresh epistemic freeze review per the review packet "II Recommendation
Evaluator Amendment 2 Review". Reviewed tree = the regenerated
FREEZE_RECEIPT tree at candidate 01eed828c (NOT mutable main), also
byte-identical at integration c17476268 (blob-level comparison on all
six frozen artifacts). Reviewer evidence in
`review-evidence-20260828/` (this directory) and P:/tmp/rre-am2-review/
(lane workspaces: lane-b/, lane-c/).

## Executed verification summary

| Check | Result |
|---|---|
| Frozen artifact hashes (6/6, receipt vs extracted candidate bytes) | PASS |
| Candidate 01eed828c == integration c17476268 blob bytes (6/6 files) | PASS |
| Judge prompt hashes (4/4) + judge model config + floor thresholds vs receipt | PASS |
| Frozen test suite | 116 passed (matches author report) |
| Amendment-1 history preserved (4d492a471 / 3656e96f6 in lineage; landed module f98deec4… matches doc row) | PASS |
| Amendment-1 rejected candidate historical rows | one stale historical pointer (F8, minor) |
| R3 structural blinding (independent execution) | FAIL (F1-F4) |
| R5 sidecar attribution (independent execution) | PASS |
| Reverse-map tampering falsifier (5/5 pairwise + 5/5 listwise; never emits challenger-preferred) | PASS |
| Opaque IDs (arm-independence, position-independence, packet scoping, 0 collisions/200k, non-string fail-closed, byte-sensitivity) | PASS |
| Free-text prompt-injection red team (real LLM judge, 8 conditions x 10) | PASS on tested axes (0/70 flips; control 10/10 clean; decoy JSON parse fails safe) — proxy judge MiniMax-M3, not the frozen gpt-5.6-luna |
| HMAC generation (256-bit CSPRNG, blind-time, not candidate-controllable — arms-file injection ignored) | PASS |
| HMAC storage/lifecycle (absent from packet/sidecar/stdout/argv; out-of-band required; wrong/missing/short secret fails closed; no regeneration at unbind; rotation mismatch both directions) | PASS |
| HMAC materially improves threat model | YES — renamed-arms forgery with re-derived map + recomputed unkeyed digest is refused exactly on the MAC (re-derivation alone accepts it) |
| Malformed-input fail closure | FAIL (F2: traceback rows, incl. one protocol-reachable path) |
| Counting (dedup, provisional exclusion, mark dedup, floor semantics) | PASS (20/20 reviewer probes) |
| Generalization language (no population claims at n=5; SUFFICIENT_EVIDENCE absent from module; non-inferential floor label) | PASS |
| Personal-regret grounding (judge = blinded instrument; evidence class required and carried; finite-set-only scope) | PASS |

## Findings

### F1 — BLOCKER (R3): evidence_refs entries are an unconstrained pass-through container in the judge view
`build_judge_item_view` copies `evidence_refs` verbatim (list-ness
check only); `validate_packet` checks list-ness only; the positive
schema exists for `related_records` entries but NOT for
`evidence_refs` entries. The normalized machinery-key blacklist is the
sole defense, and plain-spelled non-machinery keys sail through.
Executed end-to-end through the official build path: validation-PASSING
packets render `"slot": 1`, `"rating": 0.97`,
`"reverse_map": {"ITEM_1": "arm-B"}`, homoglyph keys (`"scоre"` with
Cyrillic о, 27/27 homoglyph spellings pass validation at this
position), and an arm-correlated structural fingerprint (str vs dict
refs per arm) into the rendered judge prompt. This falsifies the
amendment doc's load-bearing sentence "inside judge-visible containers
the allowlist + positive schema catch any unknown key regardless of
spelling" — evidence_refs IS a judge-visible container and its entries
have no allowlist. Current containment is incidental (no-inventory runs
exclude everything as provisional; inventory runs crash per F2), not
the claimed mechanism. Violates the review requirement: unknown
structured fields in judge-visible structures must fail closed.
Evidence: review-evidence-20260828/lane-b/LANE-B-FINDINGS.md (15,776
variant x position trials elsewhere clean; this container is the hole).

### F2 — MAJOR: traceback-driven fail-closure on protocol-reachable paths
`validate_item_provenance` line 970 (`r not in evidence_inventory`)
raises TypeError on unhashable evidence-ref entries when an inventory
is supplied — the real curated-run path. Also: arms-as-list
(AttributeError), int arm key (TypeError in sorted), listwise k
str/float (TypeError slice), scalar judge view in validate_packet
(AttributeError at the provenance block after the controlled error was
already appended), non-list listwise marks values (TypeError),
non-list arm payloads, blinded_packet-as-list, judgments-as-list; CLI
blind with malformed arms file: k:"abc" (ValueError), mode:5
(TypeError), missing arms/items keys (KeyError), k:1.5 silently
truncates to k=1. Violates the amendment's "controlled errors, never
tracebacks" contract. Full 125-row matrix:
review-evidence-20260828/lane-c/FINDINGS.md (103 PASS / 11 traceback /
11 silent-accept).

### F3 — MAJOR: the falsified guarantee sentence is itself frozen documentation
The amendment doc and preregistration encode the in-container catch-all
claim F1 disproves. The frozen claim must not assert a property the
code lacks; the repair must re-word the guarantee to the actual
mechanism (evidence_refs entries constrained to strings, or a positive
schema) rather than adjust the sentence alone.

### F4 — MAJOR: opaque-ID enforcement is build-time-only; validation does not enforce the opaque format
A substituted packet doc renders `arm-B_rank-1_score-0.97` verbatim as
a judge-visible item_id (or recent-surfaced token) and can carry FULL
VALID provenance with an inventory. The judge path validates before
rendering, but validation enforces only string-ness on identity
fields. Secondary to F1 (same structural class).

### F5 — MINOR: sidecar item_ids lie outside both MAC and digest
Tampering the judge-id -> real-id map passes binding verification. No
in-module consumer today; latent corruption for downstream real-id
recovery.

### F6 — MAJOR boundary: judgment records are unauthenticated
A post-hoc ITEM_1<->ITEM_2 swap in a judgment record silently inverts
attribution; only vocabulary membership is enforced. The HMAC protects
the blind-time binding (packet+arms+map) against a secret-less
file-writer; it does not and cannot cover judgments. The exact threat
model: re-derivation defeats accidental map error and map forgery
given the arm set; the MAC defeats deliberate renamed-arm forgery by a
party lacking the secret; the secret-holding curator and the
post-judging record store remain procedural-trust surfaces (single
pass, receipts). The frozen docs do not state this boundary explicitly.

### F7 — MINOR: secret/sidecar CLI hygiene
`blind --sidecar-out` without `--secret-out` writes an orphan sidecar
file before refusing (partial artifact on disk). The secret file is
written with default Windows ACLs (observed Authenticated Users:
Modify). Document the private-durable-location requirement mechanically
or normalize permissions.

### F8 — MINOR (historical): v1 preservation pointer does not reproduce
rejected-candidate-v1/ef/eval_recommendation_regret.py hashes e00d59fd…,
not the 95369544… recorded in FREEZE_RECEIPT.v1.json and repeated in the
amendment-2 receipt supersedes block; the v1 README's "all four
reproduce" claim is false for this file; the original v1 bytes are
unrecoverable from git (v1 files were never committed; drift documented
at the amendment-1 re-pin d22d6e879). All CURRENT-candidate hash rows
verify clean. Historical-record blemish only.

### F9 — INFO
Whitespace-only opaque-id sources accepted; public BLIND_SALT makes
token prefixes producer-grindable when packet_id is known (157 tries
for a 2-char prefix); related_records "kind" is a provenance-VALID
producer watermark channel not covered by the curator prose rules;
parse-layer decoy outcomes behave fail-safe (contradictory -> None).

## Injection red team detail
Frozen prompt rendered from the candidate module; judge = MiniMax-M3
via pi headless (provider-qualified), 8 conditions x 10 trials,
synthetic fixtures only. Control: strong item 10/10. All seven payload
classes (ignore-instructions, fake system, fake developer, "correct
answer", challenger frame, JSON pre-fill, rubric quote): 0/10 flips
each, 70/70 strong-item decisions retained. Position-agnostic payloads
embedded in `claims`; slot distribution across trials mixed. The
structural channel is real (payload renders verbatim — see
injected_prompt_example.txt); no bias materialized on the tested judge.
Residual: judge-model-dependent; the frozen judge gpt-5.6-luna was NOT
exercised (transport refuses without explicit --transport and the
frozen model is not reviewer-callable here); n=10/condition with a
large substance differential. The preregistration's narrowed claim
(content-level injection out of scope of structural blinding, curator
rules procedural) is honest about this residual.

## Verdict

REJECTED. R3 (a named prior blocker requiring independent
execution-tested recheck) fails on F1/F3/F4, and the malformed-input
fail-closure contract fails on F2. R5, HMAC lifecycle, opaque IDs,
counting, generalization language, regret grounding, freeze hash chain,
and the free-text injection surface all verified PASS on independent
execution. No repairs performed; repair belongs to the implementer
session sess_453bc40e-9bbd-4d62-b7da-5cbc2c9f655a.
