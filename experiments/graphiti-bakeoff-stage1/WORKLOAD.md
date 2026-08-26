# Workload manifest — measured 2026-08-26 (read-only, mode=ro queries)

Canonical data lives in P:/.data/yt-is/ (paths hard-coded in ef/authority.py, ef/catalog.py, ef/concept_registry.py). Engines: SQLite (WAL) + Qdrant.

| Field | Value | Source |
|---|---|---|
| Documents (transcripts cached) | 290,731 | transcripts.sqlite transcript_cache (4.2 GB), span 2026-04-25 → 2026-08-26 |
| Videos tracked | 700,156 analysis_status; video_catalog 343,119 | batch_status.sqlite (2.5 GB) |
| Evidence units | 266,945 (distinct video_ids) | catalog.sqlite eu |
| EU chunks | 895,597 (chunk table; Qdrant active_points; FTS5 898,874) | catalog.sqlite, fts5.sqlite (5.2 GB), operational-status.json |
| Concepts/entities | 7,390 surface forms; 388 clustered entities; 351 topic clusters | catalog.sqlite entities / entity_corpus / topic_clusters |
| Relationships | 131,980 kg_edges over 40,569 kg_nodes | catalog.sqlite kg_edges/kg_nodes |
| Sources/channels | 3,092 distinct channel_id in eu (1,820 with metadata, 433 blocklisted) | catalog.sqlite eu / channel_metadata |
| Ingest rate | peak ~69k EU/day (Aug 20) backlog catch-up; steady ~1.3–2k/day now | eu.built_at per-day counts |
| Retained history | ~4 months of transcripts | transcripts.sqlite min/max(cached_at) |
| Query concurrency | none recorded; single serialized query service (exclusive start-lock, ef/server.py:64); RETRIEVE_TOP_K=8 | code |
| AI-session concurrency | not recorded; proxy ~19 concurrent session dirs + 7 lock leases | repo .data/sessions |
| Replay workload | full-corpus rebuild (concept_discovery --as-of); gen-1 built 266,945 EU over Aug 17–26 | ef/concept_discovery.py, state.json |

Note: concept-registry tables (concepts, concept_aliases, concept_observations, trend_episodes) are defined in ef/concept_registry.py but NOT instantiated in production catalog.sqlite.

# Mechanism inventory (existing relational system)

| Mechanism | Class | Evidence |
|---|---|---|
| temporal facts (as-of/valid-time) | PARTIAL | concept_discovery.py:24-27 as-of cutoff; no valid_time/invalidation on concepts/relations |
| provenance (EU → assertion) | EXISTS | catalog.py:23-41 (authority_ref, content_hash); concept_registry.py:91-104 (evidence_ref); personal_graph.py:72-82 (evidence_links) |
| graph traversal | PARTIAL | graph_query.py:34-148 fixed-depth; WITH RECURSIVE only for Wikipedia backlinks (scripts/wiki_traversal.py) |
| concept identity | EXISTS | concept_registry.py:191-193 sha256 identity; :67-168 schema |
| aliases / entity resolution | PARTIAL | exact-normalized only (:200-208, :304-322); explicit operator merges (:348-411) |
| replay | PARTIAL | idempotent recompute via content-hash keys, NOT event-sourced (:443-446, personal_graph.py:237-240); state events are receipts not replay-deduped (:338-339) |
| discovery (serendipity) | EXISTS | concept_discovery.py open-world; horizon_scout.py wildcard budget; discovery_radar why_surfaced (:748-817) |
| relationship storage | EXISTS | concept_registry.py:122-132 UNIQUE triple + evidence_json; kg_edges; evidence_links |
| relationship history | ABSENT | :634-644 last-write-wins upsert, no version table |
| contradiction | PARTIAL | `contradicts` relation type + counterevidence column exist; no competing-claims model |
| supersession | ABSENT | destructive upserts everywhere; no superseded_by |
| evidence removal w/ downgrade | PARTIAL | freshness.py:309-331 deletes EU from catalog/FTS/Qdrant; NO downstream downgrade of concepts/interests |
| historical reconstruction (state at T) | PARTIAL | only by re-running discovery with --as_of; no live as-of view; mutable fields overwritten in place |
| concurrency (multi-writer) | PARTIAL | WAL + BEGIN IMMEDIATE in pipeline code (csf/batch_status.py:599-603); semantic layer single-writer (concept_registry.py:216) |
| transaction isolation | PARTIAL | generation CAS on build promotion (buildspec.py, receipt.py:110-113); none on registry rows |

Full file:line citations verified against the clean worktree by an independent Explore agent, 2026-08-26.
