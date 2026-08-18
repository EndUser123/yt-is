# EF Benchmark Protocol v1 (frozen 2026-08-17 before C7)

## Three roles

A. STABLE REGRESSION ANCHORS — C1-C6 exposed sets + fixed regression
   gates. Always run. Detect regressions. NEVER independently authorize
   promotion (stability ≠ freshness).
B. UNTOUCHED PROMOTION HOLDOUTS — sealed shards authored under this
   protocol, never retrieval-scored before consumption. The ONLY
   promotion evidence. C7 consumes shard_01.
C. CHALLENGE/DISCOVERY — deliberately weird/hard queries. Informational;
   a finding becomes blocking only if promoted into the protocol before
   the next untouched gate.

## Query contract (every judged promotion query, before retrieval)

query_text | intended_information_need | consumer_class
(yt-is direct | /wiki | /www | /review-arch) | query_style
(descriptive semantic | telegraphic-but-sufficient | technical |
comparison | ambiguous/common term) | required concepts/entities |
relevance criteria

## Blind pre-retrieval validity gate

Before any retrieval: given ONLY query_text + stated information need —
does the query itself carry enough information for a reasonable
retriever to identify what is sought, WITHOUT the originating
transcript? Reject/replace hidden-source-context queries ("part seven",
"question five" without recoverable referents). Terseness is NOT
grounds for rejection ("python async sqlite locking" is valid). Every
rejection + reason recorded.

## Frozen distribution (product-target rationale)

Proxy product mix: mostly natural/semantic consumer queries, a solid
technical share, real comparison and terse-but-sufficient usage, plus
identifier/ambiguous shapes covered by auto strata.

judged shard composition (per shard, ~55):
- descriptive semantic (yt-is + /wiki //www //review-arch): 40%  ≥0.75 any@3
- telegraphic-but-sufficient: 15%  ≥0.70 any@3
- technical: 15%  ≥0.75 any@3
- comparison: 15%  ≥0.75 any@3
- ambiguous/common term: 15% routed correctly + judged any@3 ≥0.66

auto strata (per shard): exact_df1_strong ≥25 (R@1==1.0); df2-10 strong
≥12 (literal_prefix==1.0); df11-100 strong ≥12 (==1.0); df101-1000 ≥15
(==1.0); punct ≥12 (==1.0); weak_df1 ≥10 (top3-literal==1.0 AND
prefix≥0.95); zero_df ≥10 (empty==1.0); twins (false_pin==0).

Structural/invariant gates (each battery): reopen 30/30; filter 20/20;
parity; namespace VALID; lag ≤50; restart/reconnect; latency p95
≤250ms; regression anchors (C1 strong df1 1.0; C2 strong literal 1.0;
twins 0; C2 natural MRR ≥0.65 [anchor-specific, was 0.7167 stable]).

Thresholds are product targets fixed NOW, not derived from any observed
result (per H-gate: not from C5's 0.83, not from C6's 0.63).

## Bank layout

benchmark_bank/
  exposed/ (C1-C6 archives, moved refs)
  regression/ (anchor query files)
  promotion_shard_01/ (region [415:445])  <- C7
  promotion_shard_02/ (region [445:475])  reserved, auto strata sealed
  promotion_shard_03/ (region [475:505])  reserved, auto strata sealed
  challenge/ (informational)

Shards 02/03: regions reserved + auto strata sealed now; hand queries
authored under THIS frozen protocol at consumption time, before any
retrieval against their regions (documented deviation from
"author-all-now": single-session authoring capacity; the protocol, not
the authoring session, is what stabilizes the metric).

## Consumption rule

A shard is consumed exactly once as promotion evidence. Failure on a
VALID stratum = product weakness evidence -> STOP. No threshold edits,
no shard reuse, no C8 after a C7 pass.
