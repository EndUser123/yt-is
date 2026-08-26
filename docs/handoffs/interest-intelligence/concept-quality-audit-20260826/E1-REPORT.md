# E1 — Evidence-Backed Entity Admission + Publisher Accounting (2026-08-26)

Agent: zcode (continue-existing-implementer). Decision: **E1_SUPPORTED**, implemented,
migrated to production via the established deterministic rebuild. Concept Registry NOT
deployed; E2-E5 untouched.

## Root cause trace (Step 1) — verified_fact

Entity KG nodes were created from EVERY `entity_corpus` row
(scripts/build_knowledge_graph.py, old `_write_phase`), while `mentioned_in` edges come
from a separate FTS staging requiring >= 2 matched chunks WITHIN one EU
(`MIN_EDGE_WEIGHT`). Upstream, `extract_entities.refresh_counts` admits an entity into
`entity_corpus` on LLM SELF-REPORTED mention sums (HAVING SUM(mentions) >= 2; the
prompt asks the model to estimate mentions) with NO evidence floor — it inserts rows
even when the corpus-wide FTS count is 0. Node admission and edge evidence are two
independent thresholds evaluated at different times against a moving index
(fts5.sqlite is rebuilt on ingestion/generation switches; catalog chunk table grew from
~895K at audit time). The invariant "a node exists only if at least one qualifying EU
supports it" was therefore violated by construction, not by data loss.

## Exhaustive orphan accounting (Step 2) — all 67, mechanical (measured_metric)

Re-ran the builder's exact FTS staging semantics for each orphan under current indexes:

| class | n | meaning |
|---|---|---|
| QUALIFICATION_DEFECT | 53 | corpus-wide matches exist but never >=2 chunks in any single EU (corpus pass vs edge pass threshold mismatch) |
| NO_SUPPORT_CURRENT | 13 | zero FTS matches today (stale/hallucinated LLM names admitted on self-report) |
| STALE_GRAPH_HAS_SUPPORT_NOW | 1 | gained qualifying evidence after last build ('Money Transmitter License'; its edges were simply missing from the stale graph) |

All sampled private labels stay in .data; classes above are aggregate.

## Counterfactual (Step 3) — frozen snapshot, both arms identical input

Arm A (prior behavior): 388 nodes = every entity_corpus row; edges = fresh staging.
Arm B (evidence-backed admission): nodes only where staging produced >= 1 qualifying EU.

| metric | Arm A | Arm B |
|---|---|---|
| entity nodes | 388 | 313 |
| zero-support nodes | 75 | 0 |
| mentioned_in edges | 102,454 | 102,454 |

Removal delta = exactly the 75 zero-support nodes. No supported edge changes between
arms by construction. Separately, plain index drift vs the stale production graph moves
edges 91,670 -> 102,454 for BOTH arms; 9 nodes that had edges at last build time have
no qualifying EU in the frozen snapshot (astronomy cluster incl. Hubble terms +
'NVIDIA DGX Spark') — their edge loss belongs to drift shared by any deterministic
rebuild, not to E1's floor; disclosure: 'NVIDIA DGX Spark' lost its NODE under B (it
had prod edges from the stale graph) — audited GOOD samples did not include it.

## Frozen-sample re-audit (Step 4) — same policy hash 290a3fbdd9b6a031, no redraw

Sampled entities n=51: kept 44, removed 7. Removed: 7x EXTRACTION_ARTIFACT, 0 GOOD.
GOOD-rate 0.686 -> 0.795. All five retained/non-GOOD classes unaffected. The floor
removes exactly its target class in the blinded sample.

## Publisher accounting (audit feature ONLY, never a gate)

Identity field: eu.channel_id, except discord -> guild name (channel_title carries the
guild/server; strongest available independence), hackernews/newsletter/empty ->
explicit UNKNOWN ("hn" is one aggregator field; newsletter has empty ids).
YouTube-class acquisition modalities (notebooklm/ytdlp/selenium/whisper) share UC
channel_id, so modality cannot masquerade as publisher diversity.
Coverage: 266,676 / 266,945 EUs (99.9%) get a real identity; UNKNOWN recorded for 262
hackernews + 6 newsletter + 1 legacy ytdlp_ejs row. Supported entities by known-publisher
count: 1 pub: 34 (10.9%), 2: 27, 3: 14, >=4: 238.
Stored on every entity node as meta_json.evidence {distinct_eu, distinct_publishers,
publishers_known}. No promotion/promotion-gate use anywhere.

## Downstream dry-run impact (Step 5) — read-only consumer inspection + counts

- Temporal Emergence: INPUT_UNCHANGED. Burst policy/trend alerts consume cluster/eu
  substrate; no reader of kg_nodes kind='entity'.
- Interest Inference core: INPUT_UNCHANGED. ef/interest_stats.py joins mentioned_in /
  in_channel edges; removed nodes had zero edges. Display-side provenance unchanged.
- Semantic adjacency (ef/graph_query.py): INPUT_UNCHANGED (edge-traversal queries;
  orphans were unreachable).
- Shadow discovery anchors (ef/shadow_discovery.py:222 top-50 entities by weight):
  INPUT_UNCHANGED measured — 0 of the current top-50 were unsupported.
- Warm query service entity browse/list (ef/warm_query_service.py:1248): INPUT_CHANGED
  mechanically — 75 fewer entity rows can appear in listing/search surfaces; these are
  precisely the zero-evidence artifacts. Not a metric measurement.

Verdict labels used per packet: INPUT_CHANGED / INPUT_UNCHANGED as above; no downstream
metric change claimed anywhere.

## Implementation + migration (E1_SUPPORTED branch)

Changed: scripts/build_knowledge_graph.py (+105/-13), tests/test_knowledge_graph.py
(rewritten fixtures for new semantics + 4 new tests).

Tests (32 passing across test_knowledge_graph.py, test_extract_entities.py,
test_query_filter_and_graph_conn.py) cover:
zero-support exclusion; audit block values; modality collapse (same channel via two
modalities counts ONE publisher); explicit UNKNOWN; evidence removal -> node gone;
restoration -> byte-identical node/edge set; idempotent double-build; dry-run parity.

Migration = deterministic full rebuild (KG fully derived; no bespoke surgery, no
history destroyed outside the derived tables' defined content):
before: entity nodes 388, orphans 67, mentioned_in 91,670
after:  entity nodes 313, orphans 0, mentioned_in 102,454 (drift-inclusive)
Second consecutive rebuild produced the byte-identical receipt (idempotent).
Provenance loss: none beyond the 75 zero-support derived nodes whose only stored claim
was admission itself; source tables (entities/entity_corpus/eu/chunk/fts) untouched.

## Review + publication

Lane review run approved before integration (fresh-context general-5-3-max reviewer:
re-ran the tests, recomputed every production number read-only, verified tree binding;
all kg_nodes readers repo-wide enumerated). Three disclosed minor findings, none
blocking: (F1) latent asymmetry — a dangling chunk.eu_id would create edges for an
unadmitted entity (zero such references today); (F2) meta_json built by SQL string
concatenation would emit invalid JSON if an entity_corpus label contained quotes
(pre-existing pattern, label vocabulary is six clean enum values); (F3) this report's
consumer list originally missed concept_discovery/evidence_clusters/
evaluate_concept_discovery readers (edge-traversal, equally unaffected) and the
interest_stats.corpus_summary entity-count display now shows 313 instead of 388.
No post-review code change was made, so no delta review is required.

Committed via commit_broker; integrated to main; pushed fast-forward (no force).

Concept Registry deployed: NO. E2-E5 executed: NO. Publisher count used as gate: NO.
