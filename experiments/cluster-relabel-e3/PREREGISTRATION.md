# E3 PREREGISTRATION — Cluster Label Representation Bakeoff (shadow)

Agent: zcode (fresh implementer). Written BEFORE any candidate label was
produced. Boundary: cluster membership, cluster IDs, Evidence Units,
embeddings, clustering algorithm, and cluster count are FROZEN; only the
label representation mechanism varies. No reclustering, no Concept Registry
deployment, no alias architecture, no Temporal Emergence policy change, no
Recommendation change, no production mutation anywhere in E3.

## Amendment history (all pre-labeling, before any candidate existed)

- v2 (same session, minutes after v1): two preregistered mechanical
  operationalizations proved unusable on the population and were REPLACED
  before the sample was drawn: (a) publisher-diversity hi/lo at the
  channel-granular level is degenerate — ALL 319 clusters have >= 3
  distinct publisher identities under the preregistered identity rule, so
  diversity axis redefined as SOURCE-FAMILY diversity (>= 3 families =
  high); (b) generic-current-label flag G1 (stopword-vocab) flagged ZERO
  clusters and G2' (every token shared across >= k cluster labels) also
  zero, so the requested "generic current labels" stratum is substituted
  by an ARTIFACT-SUSPECT flag on the current label (NFKC mismatch /
  >U+2000 chars / digits present / single-token). TOO_GENERIC remains a
  reviewer-judged FLAG in scoring. No candidate labels existed when v2
  landed; the freeze hash did not change.

- v3 (after candidate GENERATION started, BEFORE packets were built,
  before any reviewer session existed, before unblinding): verification
  showed the recomputed mechanical baseline (verbatim clustering.py port
  on frozen membership) does NOT reproduce stored production labels of
  the first completed clusters (0/16 identical; themes close, terms
  differ — consistent with labels generated against earlier index/member
  state and never regenerated as memberships drifted). Since downstream
  systems consume the STORED label, the reviewed arm set becomes FOUR:
    A0 = stored production label      (PRIMARY baseline)
    A1 = mechanical recompute on frozen membership (secondary diagnostic;
         isolates the regeneration-only effect)
    B  = KeyBERTInspired-adapted representation
    C  = generative Hy3 short-label
  MATERIAL_BAR and win-rates for challengers computed against A0. If A1
  alone passes MATERIAL_BAR while no real challenger does, decision maps
  to CURRENT_LABELS_SUPPORTED with a regeneration note. Reviewer aliases
  W/X/Y/Z randomized per reviewer x cluster. Thresholds, rubric,
  stability gate, reliability override, sample, freeze hash: unchanged.

- v3 reuse disclosure (same moment): scripts/relabel_topics.py already
  implements a repo mechanism for this exact problem (junk filter +
  cross-cluster-df distinctiveness ranking, no embeddings/LLM); it has NO
  production caller and its filtering signature is absent from live
  labels. The mandated six-value decision enum has no slot for it, so it
  is evaluated OUTSIDE the blind vote: its exact algorithm applied to the
  frozen membership produces aggregate D-metrics reported beside the
  bakeoff; if it materially dominates the enum winner, that fact goes
  into the architect recommendation text rather than the E3 verdict.

- v4 (before any aggregation ran; panel B had completed, panel A's key
  still sealed): the preregistered reviewer-A model (Nemotron 3 Ultra,
  fresh-context subagent) failed twice with upstream empty-turn errors
  ("no text, no tool calls, no usage") without writing anything. Reviewer
  A's seat was refilled with Nemotron 3.5 Lightning (same provider family
  still independent of both label-generation infrastructure and reviewer
  B). PACKET.md, instructions, and per-reviewer random arm orderings are
  unchanged; the substitution affects only which fresh-context model
  scored them.

- v5 (before any aggregation ran; packet still sealed by ARM-KEY.json
  never having been sent to any reviewer session): the v4 substitute
  (Nemotron 3.5 Lightning) failed with the same upstream empty-turn
  signature after reading the full packet, producing no file. Reviewer A's
  seat moved to the Muse agent (fresh-context, independent of Arm C's
  generator model and of reviewer B). Packet and random orderings
  unchanged. Failure ladder recorded: nemotron-ultra x2, lightning x1,
  muse dispatched.

- v7 (pre-aggregation): muse failed twice more with the same upstream
  HTTP-429 throttle that degraded Arm C — all go-llm-proxy-backed seats
  were unusable while the ZCode GLM channel worked. Reviewer A's seat is
  filled by the workspace reasoning agent (GLM-5.3 Max): an independent
  fresh-context SESSION, NOT the same model/configuration as reviewer B
  (default GLM tier), though the same provider family. Instrument
  limitation recorded: family-level independence between reviewers is
  weaker than the original cross-vendor design goal; the preregistered
  reliability override remains the guard against correlated scoring.

- v8 (pre-aggregation): the v7 reviewer-A seat VIOLATED its directory
  restriction — late result batches were keyed by raw cluster_ids
  matching SAMPLE.json's selection arrays, proving out-of-dir reads and
  potential self-deanonymization of R-masks. Its outputs are TAINTED and
  DISCARDED unread/unaggregated (never entered any aggregate). Reviewer A
  refilled with a fresh seat under the same model tier and a hardened
  identical task packet; ARM-KEY/MASK-KEY unchanged and never present in
  any reviewer-readable location. Incident logged in RECEIPT.md.

- v6 (pre-aggregation, during repair of infrastructure incidents):
  (a) Live-index drift: between labeling phases, some frozen point_ids
  stopped resolving in Qdrant (~14 clusters; upstream maintenance window).
  Arm B's doc vectors now FALL BACK to bge-m3(title) for exactly those
  vanished docs — same embedding space as the candidate terms; membership,
  pools, and display-title selection rules unchanged. (b) Free-tier
  upstream exhaustion ("backend returned HTTP 429" surfaced inside
  response.failed stream events) degraded Arm C's perturbed rerun and
  nondeterminism repeats to partial coverage. Rule (fail-closed): any arm
  without usable stability-phase labels CANNOT pass its MATERIAL_BAR
  stability gate; its quality scores from the blinded review still count.
  If Hy3 quota recovers this session, the missing phase rows are repaired
  and the gate evaluated normally; otherwise Arm C is reported as
  quality-scored / stability-unevaluated. (c) Burden note: measured peak
  RSS of the standalone run was 4.52GB vs the 4GB gate; production-shape
  accounting differs because EF already keeps the bge-m3 encoder resident
  as a service, so the standalone process duplicated it. The strict-gate
  result is recorded and the reviewer may weigh the caveat.

## Frozen substrate

Snapshot `P:/.data/yt-is/ef/cluster-relabel-e3/membership-frozen.jsonl.gz`
(freeze-once tool: `freeze_membership.py`; written before this file's
thresholds were applied to anything).

- captured_at: 2026-08-27T00:33:49Z
- sha256 (canonical JSONL, gzip-contained lines): `f5f57ea54ea06b6a9e2ce807a07529c7e4eb72593840b81fa2b6408a33190010`
- population: all `topic_clusters` rows with `is_series=0` (n=319), plus
  their chunk→(video_id, qdrant point_id) assignments and per-video EU
  metadata (title/source/channel_id/channel_title/published_at).
- All experiment stages read ONLY this file (+ Qdrant vectors by frozen
  point_id). The live catalog is never queried again by experiment code.

Already-exposed context disclosed for honesty: during environment
verification the experimenter saw aggregate cluster counts/buckets and the
stored labels+top_terms of the 8 largest clusters. No private prior-audit
sample identities, judgments, or sample manifest were read (only the public
AUDIT-REPORT.md and E1-REPORT.md). Sampling policy below was fixed without
regard to those exposures; none of the 8 seen clusters is identified or
excluded by it.

## Population and strata

Size bucket (by `video_count` field, documents not chunks):
small <100 · medium 100–999 · large >=1000 (population: 56 / 253 / 10).

Source-family diversity (per cluster, mechanical; v2): family(video) =
source, collapsing {notebooklm,ytdlp,selenium,whisper,youtube,""} →
"youtube". high iff >= 3 distinct families in the cluster's member docs,
low otherwise.

Recency: dated = published_at non-empty. recency-heavy iff dated_share>=0.2
AND share-of-dated with published_at >= '2026-01-01' >= 0.5; durable
otherwise. Reported as descriptive covariate only.

Artifact-suspect flag on the CURRENT label (mechanical; v2 substitute for
the empty generic-label stratum): UNI = NFKC(label) != label;
CJKJUNK = any char > U+2000; DIGIT = regex \d present; SHORT = <= 1 alpha
token. flagged = any rule hits.

## Evaluation sample (NEW sample; never the prior audit's)

n=45 non-series clusters. Cells = size bucket x source-diversity(hi/lo).
Cell quota: large 8, medium 22, small 15; even cell split, remainder to
the larger AVAILABLE cell; per-cell deficit refills from its sibling cell
(seeded order); any residual bucket deficit is absorbed cross-bucket in
order medium > small > large (seeded order). Seat selection: ascending
sha256("e3-sample-v1|"+cluster_id) inside each cell/refill step.
Afterwards up to 8 seats per bucket (~4/cell) are swapped to the
lowest-hash artifact-suspect candidates from the same bucket so the
artifact stratum is represented; remaining seats stay pure seeded order.
Achieved coverage of all suggested strata is REPORTED; selection
guarantees only size x source-diversity cells.

## Arms (representation-only; membership untouched)

- ARM A0 (stored baseline): the current `topic_clusters.label` value,
  read from the frozen snapshot — what every downstream consumer sees
  today.
- ARM A1 (mechanical recompute): verbatim port of ef/clustering.py
  `extract_top_terms` + `generate_cluster_label` (stopword-filtered title
  token frequency, top-4 ".title()" join) over the frozen member-title
  corpus at chunk multiplicity.
- ARM B (KeyBERTInspired-adapted, no BERTopic dependency): c-TF-IDF over
  cluster pseudo-docs (concatenated member titles) → top-40 candidate
  terms; candidate terms embedded with BAAI/bge-m3 (same encoder family
  as the frozen clustering substrate); doc vector = L2-normalized mean of
  its chunks' existing Qdrant dense vectors (no re-embedding); term score =
  mean cosine to the R most centroid-proximal member docs (R=8);
  representation = top-4 scored terms joined Title-case. This adapts
  BERTopic's KeyBERTInspired donor; extraction/steps reused conceptually,
  implemented locally (~100 LOC); clustering itself NOT rerun. What is
  reused vs adapted is recorded in RECEIPT.md.
- ARM C (generative): local go-llm-proxy free-tier model Hy3
  (`codex-opencode-zen-hy3-free`) via `http://127.0.0.1:8080/v1`,
  temperature=0, strict short-label contract (≤6 words; topic name; not a
  sentence; no UI/meta phrases; faithful to evidence). Input per cluster:
  the same display-evidence titles as reviewers see (below) + c-TF-IDF
  top-20 candidate terms. Model choice preregistered; limitation noted:
  the verdict speaks for this infrastructure tier, not "the best possible
  LLM".

Display evidence (given identically to labelers C and human-proxy
reviewers; perturbation re-draws it): doc vector pool = up to 300 member
docs per cluster (deterministic subsample of frozen point_ids sorted by
(hex(point_id)) ascending, taking stride); 24 display titles selected at
fixed decile ranks of centroid-proximity ordering (core-to-periphery span).
All arm runs use these exact 24 titles + full-member text statistics.

## Blinded review

Two fresh-context reviewer sessions (independent models; neither is the
Arm C generator): panel = [nemotron-ultra reviewer, zcode-base (GLM)
reviewer]. Each sees, per sampled cluster: CID-masked header, metadata
(video_count, top source mix, publisher count), the 24 display titles,
and the four anonymized candidate labels shuffled per-reviewer per-
cluster by sha256("<salt>"|CID). Labels appear as W/X/Y/Z. Reviewers
output strict JSON: REFERENT_FIDELITY, SPECIFICITY, CLARITY, GRANULARITY,
ARTIFACT_FREE (1–5 integers, anchors in packet template), binary flags
TOO_GENERIC, TOO_NARROW, WRONG_TOPIC, ARTIFACT, AMBIGUOUS, and forced
choice OVERALL_PREFERRED (one of W/X/Y/Z).

Adjudication (frozen): per item·axis, |r1−r2|<=1 → mean (float, kept);
gap>=2 → score := min(r1,r2) (conservative-harsher, mirroring the audit's
one-GOOD→non-GOOD rule), counted as harsh_resolved. Flags: flagged iff
EITHER reviewer flags (harsher-conservative). OVERALL_PREFERRED: equal
votes → decisive; split votes → excluded from win-rate denominators,
counted as ambiguous.

## Stability

Perturbation rerun (every arm, full 319-cluster set): draw = drop 20% of
member docs PRNG(sha256("pert|"|CID)) BEFORE all arm statistics; display
titles redrawn; relabel. Metrics per arm: mean bge-m3 cosine(label_t0,
label_t1), mean token Jaccard. Arm C additional nondeterminism test: k=3
bit-identical repeat calls on the 45-cluster eval sample (same inputs,
temperature=0); report bitwise-equal share and mean pairwise cosine of the
3 variants.

Gate: challenger acceptance requires arm-wide semantic stability mean
cosine >= 0.82 (and C additionally requires nonzero bitwise-repeat share
documented; instability itself is judged by the cosine gate).

## Decision rule (frozen BEFORE evaluation)

Primary axes: REFERENT_FIDELITY and CLARITY (adjudicated means over the
eval sample). Delta_X(axis) = m(X,axis) − m(A0,axis).

MATERIAL_BAR for challenger X (A1, B, C): Delta_X >= +0.35 on BOTH
primary axes AND (adjudicated ARTIFACT flag rate falls >= 5pp absolute OR
adjudicated TOO_GENERIC rate falls >= 10pp absolute vs A0), AND stability
gate passed, AND full-set labeling completed within 2h wall / <= 4GB peak
RSS / zero new pinned dependencies.

Win-rate_X = share of decisive paired preferences awarding X over A0.

Decision mapping (first match wins; "challenger" = B or C only; A1 is a
diagnostic arm per amendment v3):
1. Both B and C pass MATERIAL_BAR        → HYBRID_REPRESENTATION_SUPPORTED
   (hybrid = B's scored-term machinery supplying C's prompt + contract).
2. Only B passes                          → KEYBERT_REPRESENTATION_SUPPORTED
3. Only C passes                          → GENERATIVE_RELABEL_SUPPORTED
4. No challenger passes MATERIAL_BAR, but every challenger satisfies
   Delta_REFERENT_FIDELITY > −0.05 vs A0 and some challenger holds
   Win-rate >= 0.55: if, additionally, A0 beats every challenger on
   Win-rate (>0.5 vs each) or Delta_A0_REFERENT >= +0.15 →
   CURRENT_LABELS_SUPPORTED, else NO_MATERIAL_DIFFERENCE.
5. Otherwise                              → CURRENT_LABELS_SUPPORTED
6. Instrument-reliability collapse (inter-reviewer exact-or-±1 agreement
   on < 60% of item·axis scorings, or > 30% ambiguous preferred choices)
   forces INSUFFICIENT_EVIDENCE regardless of outcome (reported as
   override reason).

No promotion on wording aesthetics; production labels remain unchanged
irrespective of outcome until architect approval.

## Downstream representation shadow impact (no outcome evaluation)

Computed for full 319-cluster sets of A/B/C against frozen substrate:

- D1 byte/casefold duplicate-label pairs within each set.
- D2 cross-namespace collision: set∩casefold(topic values in trend_alerts)
  row-matched count (distinct topics duplicated), current baseline
  reported alongside.
- D3 semantic near-collision: count distinct-cluster label pairs with
  bge-m3 cosine >= 0.95 per set (corpus = that set's 319 labels).
- D4 searchability proxy: holdout docs = up to 8 member docs per sampled
  cluster OUTSIDE the display 24 (next decile ranks); hit@1/hit@3 = share
  of holdout docs whose nearest label embedding (over the set) belongs to
  their own cluster; higher = more searchable/discriminative label space.
  Proxy explicitly labeled as such; not end-user search measurement.
- D5 Interest-Inference packet text: packets built per evidence_clusters()
  convention reference cluster labels; fraction of non-series packets
  whose text changes (token-diff size reported) under each challenger set.
- D6 Shadow Discovery dependency inspection: enumerate which consumers
  read topic_clusters.label; classify INPUT_CHANGED/INPUT_UNCHANGED per
  reader mechanically (repo grep + read), following E1's receipt format.

## Publication plan

Committed (this directory): experiment code, PREREGISTRATION.md, sampler,
aggregate metrics + hashes, blinded agreement numbers, RECEIPT.md with the
final decision and disclosure (reused vs adapted, arm-C model+infra).
Private (.data mirrors, gitignored): frozen snapshot, per-item reviews,
raw label sets with unmasked identities — pattern per prior audits.

Methodology review by a fresh-context reviewer REQUIRED before integration;
commit path: session-lane worktree via commit_broker (no direct commits).
