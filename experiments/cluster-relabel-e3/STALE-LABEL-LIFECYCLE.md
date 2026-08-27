# E3.1 STALE-LABEL LIFECYCLE — measured facts + invariant design

Agent: zcode. Read-only study per architect directive; NO deployment.
E3 established the headline: stored labels == recomputed current-membership
labels for 0/319 non-series clusters.

## Measured lifecycle facts (live catalog, read-only, 2026-08-27)

1. SINGLE GENERATION: all 319 non-series cluster labels have
   created_at = updated_at = 2026-08-20 (one platform-build batch);
   eu table holds only build_generation 1.
2. CONTINUOUS DRIFT: 301/319 (94%) clusters have at least one
   chunk_clusters assignment newer than their label's updated_at. Under
   the current scheduler this is structural, not incidental:
   YtisIndexIncremental/assign-new appends chunks daily, so effectively
   every living cluster is perpetually stale w.r.t. its stored
   representation by the next daily cycle (measured staleness saturates
   at the 1-day assignment cadence; min 0.2d / median 1.0d / max 1.0d;
   the 18 non-stale rows are same-day arrivals).
3. MECHANISM (code path, ef/clustering.py): `run_clustering` (full)
   DELETEs topic_clusters/chunk_clusters and INSERTs fresh rows whose
   cluster_id IS the raw HDBSCAN integer — so across reclusters,
   (a) labels are regenerated only in a full pass and
   (b) cluster identity itself is NOT guaranteed to survive
   recomputation (id stability depends on HDBSCAN label integers, which
   are order-dependent). `assign_new_chunks` (incremental) appends
   chunk→cluster rows using nearest-centroid on the STORED centroid but
   NEVER touches label/top_terms/description.
4. CONSEQUENCE: the stored label describes the 2026-08-20 embedding/
   clustering snapshot while 94% of clusters' memberships have since
   grown — consumers (interest packets, /topics,/trends pages, trend
   topic identity, shadow query anchors) display historical labels as
   if current. E3's A1-vs-A0 divergence (0/319 byte-equal) is this
   drift made visible.

## Invariant design (proposal; NOT deployed)

Binding rule:

    representation binds to membership_version =
        sha256(canonical(cluster_id -> sorted set(member point_ids)
                         + clustering_run_id))

State machine per cluster:
    CURRENT     label.membership_hash == present hash
    STALE       hashes differ (label kept, flagged stale_for_consumers)
    REGENERATING scheduled background job holds lock

Mechanics:
1. On every store_clusters/assign_new_chunks write, recompute
   membership_version for touched clusters and persist it alongside the
   label row (new column; additive migration).
2. Consumers that render labels MUST join on the state column or expose
   "(stale)" markers; no silent historical display (prereg contract).
3. Regeneration hook: when STALE share of touched clusters exceeds a
   policy threshold OR cluster age-in-stale > N days, schedule relabel
   for those clusters only (representation-only op; membership frozen
   during regen). Full HDBSCAN recluster remains a separate explicit
   operation because it does not preserve identity.
4. Identity hardening prerequisite: cluster_id must become a durable
   key independent of HDBSCAN integers (assigned uuid/slug at creation;
   old id kept as alias) BEFORE any relabel pipeline claims to update
   "the same" cluster across reclusters.

Out of scope here: implementing any of the above (architect decision),
choosing regeneration provider/policy, alias architecture (explicitly
out of E3 boundary as well).

## Deliberately not done

No schema change, no writes to catalog.sqlite, no consumer edits.
