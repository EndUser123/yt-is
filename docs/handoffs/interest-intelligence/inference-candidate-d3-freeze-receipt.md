---
agent: zcode
host: zcode
created: 2026-08-27
session: sess_ab0a0135-9c07-432c-af42-c9305e09195e
status: FROZEN
---

# INFERENCE CANDIDATE D3 FREEZE — RECEIPT

Machine-readable identity:
`inference-candidate-d3-freeze.json`
(implementation_manifest_sha256
3652da82817f5a373eef1011312e756fa1c42ef41d8635b553a9ab3d3e996ba3).

Architecture: D3_DECOMPOSED at commit
02f56240fc33d6cbfc493e1e25bb70742b416144; full chain 959cd1ad ->
831944e5 -> 4008a701 -> 02f56240; results receipt 5c6e4166. Manifest =
six load-bearing files with git-blob + content sha256 each.

Runtime configuration reconstructed from the shadow artifacts themselves
(plan.json, persisted schemas, per-call prompt files, driver constants):
codex-cli 0.149.1 / gpt-5.6-luna / reasoning medium / strict
--output-schema / batch <= 25 / universe 319 / plan_01b09359b3f05784.
config_identity_complete = true.

Contestant set (payload_canonical_sha256; reconstruction-verified against
the strict frozen validator during freeze):

    shadow_1 8eafbfd8cf05bb39... (canonical_objects 225)
    shadow_2 acbbda7c9c9cee14... (canonical_objects 307)
    shadow_3 277a1a3af5c22537... (canonical_objects 288)

BINDING RULE: ISEM scores ALL THREE. REPEATABLE_PERFECT iff finite-set
conformance is PERFECT on all three; any IMPERFECT -> NO. No majority
vote, no reruns after holdout unseal, no semantic selection.

Label-free stability (descriptive only): exact normalized-name 3-way
intersection 8 of union 500 (IoU 0.016); pairwise Jaccards
0.0683 / 0.0493 / 0.0582; relation/link overlaps ~0 (see JSON block
label_free_stability_receipt); 8 same-name objects show semantic-field
disagreements. Run-to-run semantic stability is NOT established — that
measurement belongs to ISEM over this frozen contestant set.

Ground truth untouched: P:/.data/yt-is/private/ not opened; no labels,
no evaluator output, no tuning after the shadow gate.
