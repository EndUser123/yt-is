# /wiki Evidence Fabric Integration — implementation handoff (design seam only)

K-gate #7. Prepared after operationalization; no /wiki changes made.

## The seam

One CLI, mirroring the workspace-canonical `wiki_search.py` conventions
(positional query, `--top-k`, `--format text|json` default json, exit 0
on empty, exit 2 usage):

```
python P:/packages/yt-is/bin/ef-query "<query>" [--top-k 8]
         [--channel-id X] [--exact] [--format json|text]
```

Backed by `ef.query_server.ProductionQuery` (active generation via
`buildspec.active_generation()`). Output rows are `EvidenceResult`
fields plus qmd-compat keys. The routing (semantic/identifier/
ambiguous/comparison/exact) is internal — consumers pass intent-free
text and optional `--exact`.

## The three maintenance modes

| Mode | Query shape | EF primitive |
|---|---|---|
| `wiki_evidence` | the wiki page's claim/summary sentences | `relevant()` per claim; top-k chunks + URL + char span as receipts |
| `wiki_contradiction` | claim + "counter-evidence/arguments against" framing | `relevant()` on contrast-framed claim; consumer-side filter: hits whose channel/category differs from the claim's sources |
| `wiki_staleness` | claim terms | `relevant()` + compare hit `captured_at`/`published_at` against wiki `last_verified`; newer contradicting evidence flags stale |

A/B rule (per K-gate): ordinary /wiki lookup stays on wiki-native
`wiki_search.py`; EF-backed modes run alongside for one operator
review cycle before any replacement decision.

## Prerequisites verified

- readiness contract (`ef/readiness.py`): callers check
  `readiness.json` state == ready before querying (degraded = Qdrant
  down; queries will still attempt reconnect).
- latency: warm p95 ~195ms; cold-start absorbed by warmup lifecycle.
- freshness: `operational-status.json` exposes lag for the monitor.

## Non-goals for the follow-on

Do not reopen generation-1 acceptance; do not modify routing; do not
consume shards 04/05 (reserved for an encoder/consumer-driven upgrade
gate).
