M-gate is a clean success. **`KEEP ALONGSIDE` is the right verdict.** I would not revisit Evidence Fabric architecture or native `/wiki` lookup now.

The highest-value next step is a **larger, real maintenance evaluation**, but I would make one small optimization first: batch the staleness timestamp lookups. Five-to-seven seconds is slow enough to distort how often operators choose to use the mode, and the cause is already known.

## Next workstream: N-gate — maintenance utility evaluation

The question should now change from:

> “Does Evidence Fabric integrate correctly with `/wiki`?”

to:

> **“Does Evidence Fabric materially improve real wiki maintenance decisions?”**

I would measure outcomes such as:

```text
claims examined
candidates retrieved
candidates reopened
useful evidence
new supporting evidence
qualifications/boundaries discovered
credible contradictions
staleness reviews triggered
actual wiki changes
no-action decisions
irrelevant/noisy candidates
operator time / latency
```

The key metric is **operator action rate**, but “no action” can also be a successful result if the evidence gave enough confidence to leave a claim unchanged.

### Before the larger review

Optimize only the known mechanical bottleneck:

```text
current:
candidate 1 → catalog reopen
candidate 2 → catalog reopen
candidate 3 → catalog reopen
...

target:
candidate IDs
    ↓
one batched catalog lookup
    ↓
timestamps/provenance attached
```

Do not change EvidenceResult or retrieval behavior.

Then run a substantially larger sample—I'd target **30–50 real wiki claims**, stratified rather than cherry-picked.

Include:

* recently verified claims;
* old claims;
* claims with strong existing evidence;
* claims with weak/sparse evidence;
* technical implementation claims;
* architectural decisions;
* claims likely to have changed with software/model versions;
* claims where contradiction would matter.

## What I would send Zcode

> **M-gate accepted. Verdict `KEEP ALONGSIDE` stands. Proceed to N-gate: real-maintenance utility evaluation.**
>
> This is **not** another Evidence Fabric architecture task and **not** a native `/wiki` replacement decision yet.
>
> The goal is now:
>
> > Determine whether EF-backed maintenance materially improves real `/wiki` maintenance decisions at useful cost.
>
> Preserve:
>
> ```text
> ordinary lookup → wiki_search.py
> wiki_evidence
> wiki_contradiction
> wiki_staleness
> /wiki-owned validation
> freshness-aware absence semantics
> generation 1
> ```
>
> ### 1. Fix only the known staleness latency bottleneck
>
> Current 5–7s staleness latency is attributed to per-candidate catalog timestamp/provenance reopens.
>
> Implement a batched lookup using the existing catalog authority.
>
> Requirements:
>
> * no EvidenceResult contract change;
> * same authoritative `captured_at` semantics;
> * exact same output/provenance;
> * focused equivalence test;
> * measure before/after latency.
>
> Do not optimize retrieval itself.
>
> ### 2. Define the operator-review sample before running it
>
> Select approximately 30–50 real wiki claims with explicit strata, including:
>
> ```text
> recent vs older last_verified
> strong vs weak existing evidence
> technical claims
> architectural claims
> version-sensitive claims
> claims with plausible competing evidence
> ```
>
> Record the sample-selection protocol before seeing EF results.
>
> Avoid selecting only claims expected to benefit.
>
> ### 3. Run all applicable maintenance modes
>
> For each claim, record:
>
> ```text
> claim/page
> mode
> candidates returned
> candidates reopened
> validated disposition
> latency
> resulting operator decision
> ```
>
> Validation dispositions should preserve the current `/wiki` vocabulary, including:
>
> ```text
> supports
> qualifies
> contradicts
> irrelevant/insufficient
> ```
>
> Staleness remains a review signal, not an automatic truth change.
>
> ### 4. Measure decision utility
>
> Aggregate at least:
>
> ```text
> useful_candidate_rate
> irrelevant_candidate_rate
> claims_with_new_support
> claims_with_material_qualification
> claims_with_credible_contradiction
> claims_flagged_for_staleness_review
> wiki_change_rate
> deliberate_no_action_rate
> average candidates reopened per useful decision
> latency by mode
> failure/degraded rate
> ```
>
> Also distinguish:
>
> ```text
> EF produced useful evidence
> but no wiki edit was appropriate
> ```
>
> from:
>
> ```text
> EF produced no useful evidence
> ```
>
> Do not equate wiki-edit rate with total utility.
>
> ### 5. Record actual maintenance consequences
>
> For each claim, classify the eventual operator action:
>
> ```text
> no_change_confirmed
> strengthen_evidence
> qualify_claim
> revise_claim
> mark_for_reverification
> remove/deprecate_claim
> defer_insufficient_evidence
> ```
>
> Do not bulk-update wiki content automatically unless the existing `/wiki` authority workflow already permits the action.
>
> ### 6. Compare modes
>
> Determine which of:
>
> ```text
> wiki_evidence
> wiki_contradiction
> wiki_staleness
> ```
>
> creates meaningful maintenance value and where each creates noise.
>
> Identify whether some modes should be invoked routinely and others selectively.
>
> ### 7. Native lookup remains unchanged
>
> Do not replace `wiki_search.py` in N-gate.
>
> This review is primarily about **maintenance**, not ordinary lookup.
>
> If enough naturally comparable cases arise, record native-vs-EF observations, but do not force an artificial replacement decision.
>
> ### 8. Terminal recommendation
>
> End with one evidence-backed recommendation:
>
> ```text
> KEEP ALONGSIDE
> EXPAND EF MAINTENANCE
> MODIFY CONSUMER WORKFLOW
> BEGIN NATIVE-LOOKUP REPLACEMENT EVALUATION
> ```
>
> Explain separately for each maintenance mode if warranted.
>
> Do not reopen Evidence Fabric generation-1 acceptance absent a genuine contract failure.

If this larger review shows substantial action/confirmation value, **then I would make EF-backed maintenance a normal `/wiki` capability rather than an experimental side path**. Only after that would I spend effort asking whether Evidence Fabric should replace ordinary `wiki_search.py`.
