# EF Benchmark Protocol v1.2 (frozen before shard03/04/05 authoring)

Supersedes v1/v1.1 gate taxonomy. All query-authoring, validity,
distribution, sealing, and judging rules unchanged.

## Core principle (J-gate)

Automatic route labels are implementation details and must NOT be
promotion gates. Routes are acceptance-relevant only when the caller
explicitly selects semantics (exact=true, quoted literal).

## Gate taxonomy

REPLACED (behavioral gates apply regardless of internal route):
- "must route semantic/ambiguous/identifier" for auto-classified input.

KEPT (caller-selected or invariant):
- exact=true / quoted literal -> deterministic literal-only semantics.
- df=1 unique literal -> R@1 = 1.0 (any route, incl. ambiguous lane's
  singleton pin).
- multi-hit literal bands -> literal_prefix@K = 1.0.
- zero-literal identifier intent -> primary empty (no false exact).
- near-twin mutant -> no false primary pin.
- conventional/technology terms (Node.js, VPN, TikTok, MongoDB, ...):
  judged relevance >= threshold; literal discoverable when literals
  exist; semantic variants retrievable; no semantic result mislabeled
  exact. The internal route is unconstrained.

## Node.js behavioral verification (exposed data, J-gate rule 5)

Node.js / Next.js / Vue.js / React.js under current implementation:
top-3 all literal-containing, exact-match labels correct (only true
literal hits carry the exact path). No retrieval-code change made.

## C8 record

C8 = FAIL (route-label gate mismatch). Behavioral retrieval properties
green. Generation 1 inactive at that time. Not retroactively passed.

## Bank replenishment (before C9)

shard03 [475:505], shard04 [505:535], shard05 [535:565] — all authored
and sealed under v1.2 BEFORE any C9 retrieval observation. C9 consumes
shard03 only.

## C9 gates

As v1.1 behavioral set with route-label gates removed:
- exact_df1_strong R@1==1.0 (n>=24); df bands literal_prefix==1.0;
  punct==1.0; weak_df1 (mixed+ALLCAPS) top3-literal==1.0 AND
  prefix>=0.95; ambiguous_allcaps_df1 R@1==1.0; conventional terms
  (incl. dotted names): literal discoverable AND judged any@3>=0.66;
  zero_df empty==1.0; twins 0; judged descriptive>=0.75,
  telegraphic>=0.70, technical>=0.75, comparison>=0.75; latency
  p95<=250ms; reopen 100%; filter 100%; parity; namespace; lag<=50;
  restart/reconnect; anchors (C1 strong df1 1.0; C2 strong literal 1.0;
  twins 0; C2 natural >=0.65).

PASS -> promote (no C10). FAIL -> STOP, shard03 consumed evidence.

agent: zcode · 2026-08-17
