# Temporal EU Time-Policy — Final Receipt

agent: zcode
Session: NEW IMPLEMENTER OPTIONAL (undated-EU temporal semantics) completed
Date: 2026-08-26
Run artifacts: `.logs/temporal-time-policy/timepolicy-20260826T-01/`
  (snapshot.sqlite + manifest.json + arm_*.sqlite + replay_wonly.json +
   replay_kasof.json)
Machine-readable decision: `time_policy_decision_v1.json` (same directory)

## Corpus (catalog.eu, frozen snapshot 2026-08-26)

| total EUs | authoritative dated | undated | recovered by experiment | approximate |
|---|---|---|---|---|
| 266,945 | 225,149 | 41,796 | 28,611 | 732 flagged |

Undated by source: notebooklm 13,110 · discord 27,740 · dht-artifact 732 ·
hackernews 74 · rss 66 · ytdlp 57 · github 8 · whisper 2 · newsletter 6 ·
podcast 1.

## By-source timing semantics (mechanically traced, not inferred from names)

- YouTube (`notebooklm/ytdlp/selenium/whisper` sources):
  `published_at` = VALID_TIME via `analysis_status.published_at`;
  `captured_at` = RECORDED_TIME (`transcript_cache.cached_at`).
  The 13,110-notebooklm undated block is NOT internally recoverable
  (0/13,110 rows have any publish field left upstream).
- Discord DHT windows (`discord`, terminal dht):
  valid time EXISTS — `transcript_cache.metadata_json.first_ts/last_ts`
  written by `scripts/run_dht_ingest.py` straight from the archive's
  `messages.timestamp` (epoch-ms, all 318,914 live-archive messages carry
  it) — but `ef/ingest_connectors.py:128` hardcodes `published = ""`,
  so the EU layer loses it. 100% cache coverage.
- dht-artifact: extractor's downloads-table path loses message_id
  (stored as 0); recovery runs through the raw-archive
  `attachments.normalized_url → messages.timestamp` bridge, falling back to
  decoding the CDN URL's attachment snowflake (APPROXIMATE, sub-minute).
- Reddit/HN/RSS/GitHub/newsletter/podcast: VALID_TIME when metadata carries
  it; small parse-miss residuals (≤74 per source).

## Discord recovery validation (deterministic sample)

- Windows: cached `first_ts` vs snowflake-decoded anchor id:
  **121/121 exact equal, delta 0 ms**; range sanity 2019-11-20…2026-08-25.
- Artifacts: bridge timestamp == URL-snowflake date on samples
  (e.g. 2022-11-06); synthetic sha256 attachment ids are never decoded.

## Bitemporal contract (documented for every EU class)

- `valid_time`: when the evidence was created/published/true.
- `recorded_time`: `captured_at` — when yt-is learned/stored it.
- Rule demonstrated on live data: captured_at must NOT silently stand for
  valid_time (Discord lag p50 = 1,262 days; 93.5% of windows > 2 years).
- Retrospective replay requires BOTH predicates:
  world-as-of (`valid_time <= t`) AND knowledge-as-of
  (`recorded_time <= t`). Pure-valid-time replay counts late-arrival
  mentions before they were known (look-ahead): e.g. C-arm v1-emerging =
  11 on 2026-08-15 without knowledge filtering vs 0 with it
  (18 on 2026-08-19).

## Arms (production scoring code verbatim on frozen snapshots; no policy edits)

Final day 2026-08-26:

| Arm | mentions | v1 emerging | v2 positive |
|---|---|---|---|
| A current | 102,454 | **112** | **176** |
| B exclude | 58,865 | 9 | 39 |
| C recover | 102,454 | 21 | 100 |
| D interval-end | 102,454 | 22 | 101 |

Counterfactual impact:

- A fabricates a burst on the bulk-capture days: emergence count
  11 → 56 → 97 across 08-20/21/22 and stays saturated (full sweep table in
  `replay_wonly.json`, arms/candidates/promote aggregates inside the JSON
  artifact). Correct time semantics removes **91 false emergences
  (−81%)** and 76 false v2-positive flags (−43%).
- Candidate episodes / promote events over the sweep: candidates
  3,053 → 2,979; promote events **1,414 → 1,185** (−229 false promotions).
- `first_seen` shifts: 53/313 entities become genuinely older under C.
- Exclusion destroys signal instead of fixing it (B retains only ~39%
  of C's temporal support); interval endpoint choice is immaterial
  (D differs from C by ≤1 entity).

## No-look-ahead verdict

PASS under bitemporal evaluation (tests case 1/2/6 enforce it);
the CURRENT production observation rule has a documented look-ahead
defect when used retrospectively because it substitutes recorded time for
unknown valid time and cannot express knowledge-as-of at all.

## Discriminating tests

`tests/test_temporal_time_policy.py` — 14 passing cases, including every
packet-required scenario 1–7 (published-yesterday/ingested-today,
year-old-ingested-today, unknown-valid, recovered-discord-time,
interval-only endpoints, future ingestion leak guard,
ingestion-stamped false burst prevention) plus recovery-method matrix and
baseline-window preservation.

## Decision

**MIXED_SOURCE_POLICY_SUPPORTED**

- Exact recovered valid time where mechanically available
  (discord windows 100% exact; dht-artifact exact/approximate-flagged);
- authoritative published_at elsewhere;
- explicit exclusion-from-temporal for genuinely unknown-valid evidence
  (notebooklm block and small residuals), never captured_at substitution.

## Production changed

NO — all arms exist only in isolated frozen snapshots; no production DB,
no policy module, no threshold was touched. Migration design (provenance-
carrying, no-history-rewrite, versioned/idempotent) is specified inside
`time_policy_decision_v1.json` § proposed_migration_NOT_APPLIED and waits
on the architect hand-back, coordinated with the parallel Temporal Emergence
model-generation lane via that same consumable artifact.

## Found-blocking-defect note (out-of-lane, reported not fixed)

`ef/concept_discovery.py` imports `evidence_cluster_inventory`, which
the CURRENT working tree of `ef/evidence_clusters.py` does not export
(it exports `evidence_clusters`; fresh-context review confirmed this is
pre-existing working-tree state predating this lane, not damage from
it) — every `scan_internal()` currently fails at import. The counterfactual
used an inert import shim solely to exercise the scoring functions;
every replay result records `concept_discovery_import_shim_applied`. The
Temporal-model lane owns the fix; retry-volume alert correlation with
YtisContentSync/YtisIndexIncremental nightly failures (exit code
0xC0000005-class) should be checked separately.

## Review

Fresh-context review packet: review inputs = this receipt, the JSON
artifact, script + test files, and run artifacts listed above.
