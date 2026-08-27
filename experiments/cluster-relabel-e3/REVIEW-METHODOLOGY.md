# Fresh-context methodology review — E3 cluster label bakeoff

Reviewer: independent general-purpose session (fresh context; read-only
over the experiment directory + public mirrors + spot-checks of private
.raw artifacts under P:/.data/yt-is/ef/cluster-relabel-e3/).

## Verdict: APPROVE_WITH_NOTES (no blocking findings)

Independently recomputed from raw artifacts before approving: agreement
fractions (.9767 within±1 / .0233 harsh / 0 ambiguous), all axis means,
all flag rates, preference distribution 43/1/1/0, win-rates, D0=0/319,
D1 counts, and D2 baseline (16/16 trend topics re-derived read-only
from catalog.sqlite) — all match RECEIPT.md and published aggregates
exactly. Decision mapping verified to follow the frozen branch-4 chain.
Blinding integrity verified: packets carry no arm identity; ARM-KEY/
MASK-KEY outside reviewer dirs; the tainted seat's output quarantined
and excluded from aggregation. Zero references to the private prior-
audit packets in any experiment code. Prereg v6 fail-closed applied
conservatively (anti-promotional direction only).

## Findings (all minor; disposition)

1. Arm C stability gate bookkeeping vs prereg nondeterminism clause —
   FIXED post-review: aggregate_reviews.py implements the frozen clause;
   C's gate is now recorded False; outcome-invariant either way.
2. Amendment ordering rests on mtimes (prereg committed at session end)
   — acknowledged in RECEIPT incidents §6.
3. Burden clause = one shared standalone-process measurement applied to
   all arms; cumulative wall across repairs not folded into elapsed_s —
   acknowledged in RECEIPT incidents §6.
4. Stability-table wording for Arm C — FIXED post-review.
5. t0 Arm-C blanks (11) understated by an earlier receipt phrasing —
   FIXED post-review ("Downstream representation impact" header).
6. arm_c.clean_output 8-word trim vs <=6-word prompt contract — kept as
   defensive bound; breaches flow through unmodified (scored as-is);
   disclosed in RECEIPT incidents §6.

All fixes after review were exactly these reviewer-requested edits plus
this file; no numbers changed (decision reproduced identically on the
fixed aggregator).
