# Design: Interest Graph — inferred durable interests over the yt-is corpus

agent: zcode | host: both | status: v1-shipped-observed-layer | created: 2026-08-24
operator-spec: preserved below verbatim (source: operator prompt 2026-08-24)

## Operator spec (verbatim, condensed only where tabular)

Analyze the corpus to infer durable interests, emerging interests, and
high-value information needs — not to summarize categories or count
topics. Infer what the user is trying to learn, accomplish, decide,
monitor, improve, build, prevent, or understand. Separate:

- core durable interests (broad domains, repeated independent evidence)
- specific subtopics
- goals/problems (outcomes the user appears to care about)
- methods/approaches (techniques, frameworks, tools repeatedly investigated)
- entities being monitored (people, projects, companies, technologies,
  diseases, markets)
- emerging interests (recent topics with growing evidence)
- historical/dormant interests (previously strong, no longer active)
- cross-domain themes (latent interests connecting different topics)
- negative evidence (frequent but not genuine interest: incidental
  subscriptions, broad channels, entertainment)

Per interest: canonical name; hierarchy (domain→topic→subtopic);
evidence with source and recency; breadth (independent channels);
intensity/depth; persistence; confidence; stance (curiosity | active
learning | active project/decision | ongoing monitoring | entertainment);
related interests that are one underlying need. Do not equate frequency
with importance. Look for LATENT GOALS (sleep + exercise + ApoB +
Alzheimer's biomarkers + glucose control ⇒ healthspan and prevention,
even with no channel using that label). Machine-readable graph for
driving retrieval/briefing; evidence preserved for audit. Final
question: "what would this user regret not being told about?" — adjacent
topics poorly represented but strongly implied (labeled inferred-adjacent,
not observed).

Operator architecture note: channels/videos → metadata + representative
content → embeddings/entities/topics → evidence clusters →
temporal/behavioral statistics → LLM interpretation → interest graph.
The LLM is the semantic inference layer, NOT the clustering/counting
engine. Interest object: domain, topic, subtopics[], underlying_goals[],
information_needs[], evidence[], observed_vs_inferred,
curiosity_vs_action, intensity, persistence, recency, confidence,
related_interests[], exclusions/counterevidence.

## Inventory (2026-08-24)

Exists: entities+entity_corpus (6.3K), topic clusters (351), kg v1 with
lift co-mentions, cross-source docs (youtube/discord/reddit/rss/hn/
github/podcast), published_at/captured_at for temporal stats, QA
provider chain (codex→agy→openrouter→gemini, quality-gated).
Missing: per-interest temporal stats, the LLM interpretation layer,
the interest-graph store, the surface, negative-evidence heuristics.

## Architecture

```
entities + topics + kg  ──(1)──►  interest_stats.py  (mechanical layer)
                                     per entity/topic: breadth (distinct
                                     channels), depth (chunk hits),
                                     persistence (active months),
                                     recency (last-seen), sources[]
(1) = v1 SHIPPED: observed layer, pure SQL, no LLM
interest_stats ──(2)──►  LLM interpretation (v2): provider chain gets the
                          top-N evidence pack, returns the interest graph
                          JSON (stances, latent goals, cross-domain
                          themes, negative evidence, adjacent/regret)
(2) = v2 (designed, not built): ef/qa.py provider chain, schema below
graph JSON ──►  interests table (catalog.sqlite)  ──►  /interests page
```

## v1 scope (shipped)

- ef/interest_stats.py: entity-level observed stats + corpus-wide
  package. Honest label: OBSERVED (counts and time spread), no stance/
  confidence/latency claims.
- /interests page: cross-source entities ranked by a composite
  (breadth-weighted), each with breadth/depth/persistence/recency and
  source chips. Observed-layer banner.

## v2 scope (designed; next slice)

- interests table schema:
  interests(interest_id PK, name, kind [domain|topic|goal|method|
    monitor|adjacent|negative], parent_id, stance, confidence,
    intensity, persistence, recency, observed_vs_inferred,
    evidence_json, related_json, exclusions_json, updated_at)
- inference script: scripts/build_interest_graph.py — packs top-N
  stats + representative doc titles into the QA provider chain prompt;
  parses the graph JSON; validates schema; writes the table.
- page upgrade: stance/confidence badges, domain hierarchy, adjacent
  ("regret") section, evidence expanders.
- refresh: manual command first; schedule after quality review.
- driving surfaces (later): digest selection, retrieval boost, agent
  research routing — all read the interests table only.

## Placement (operator: "skill or web page")

Substrate + page in yt-is package; refresh command documented in the
ytis-continuous skill; no standalone skill until a second consumer
exists (one command does not need a skill wrapper).

## Falsifier

v1: stats must reconcile with direct SQL (breadth = COUNT(DISTINCT
channel) on entity_corpus joins — spot-checkable). v2: every interest
must carry evidence rows auditable back to entities/channels; an
interest with no evidence row is a bug; stances inferred where evidence
is counts-only must carry confidence <= medium.
