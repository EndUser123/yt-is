# Evidence Fabric — D-Gate Decision (retrieval-contract correction)

Recorded from operator message 2026-08-17 (abridged to binding items;
full text in session history):

Core principle: document frequency may influence ranking and evaluation,
but must NOT determine query intent.

Production routing contract:
1. explicit exact (exact=true / quoted literal): literal matches ONLY,
   no semantic fill; rank exact matches usefully.
2. high-confidence identifier (whole-query identifier syntax, ANY df):
   literal containment priority; if exact matches >= K, rank WITHIN the
   exact set using semantic/sparse relevance; else exact first, semantic
   fills remaining slots.
3. normal semantic query: BGE dense + learned sparse, weighted fusion
   (D_weighted), exact-containment invariant never overrides here.

Authorization:
1. C1 failure + sealed set preserved as permanent regression evidence;
   generation 1 remains inactive.
2. Replace df>100=>semantic with intent-based routing.
3. Semantic ranking inside exact-containing candidates; fill only when
   short of K; strict exact stays literal.
4. D_weighted stays for semantic/fusion; must not override containment
   invariant.
5. Evaluation by query semantics: df=1 -> R@1; low-df -> literal
   coverage; high-df -> containment@K + relevance among literals;
   common/natural -> judged relevance.
6. Freeze policy-invariant ANN legs (measure once) instead of tolerating
   their variance.
7. Commit revised contract + tests + NEW preregistered gates BEFORE
   constructing another holdout.
8. Fresh untouched acceptance region; C1 = regression-only.
9. Battery: regression + fresh acceptance + reopen + filters + namespace
   + incremental lag=0 + latency + Qdrant restart/reconnect test.
10. Promote iff all gates pass (gen0 retained for rollback). No vector
    rebuilds without representation-level evidence.

Also: cached Qdrant clients must recover automatically after server
restart (no per-query PowerShell).

agent: zcode · host: both
