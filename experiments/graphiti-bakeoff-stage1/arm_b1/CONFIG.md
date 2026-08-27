# Arm B1 — exact pinned configuration

agent: zcode
Executable source of these pins: `b1_clients.py` (single place). graphiti-core
exercised through its REAL semantic pipeline: every evidence unit enters via
`Graphiti.add_episode(...)` with raw evidence text only. No `add_episode_bulk`,
no hand-built EntityNode/EntityEdge ingestion (that was the B0 diagnostic).

## Provider / model

| Pin | Value |
|---|---|
| LLM endpoint | `http://127.0.0.1:8080/v1` (local go-llm-proxy) |
| LLM client class | `graphiti_core.llm_client.openai_generic_client.OpenAIGenericClient` |
| model | `nemotron-3-5-lightning-free` (canonical wire form; live-verified 2026-08-26) |
| small_model | same string |
| temperature | `0` (LLMConfig(temperature=0)) |
| max_tokens | library default 16384 |
| api_key | env var `PROXY_API_KEY`, read at RUNTIME ONLY; never written to any file |

## Structured-output mode (what actually worked)

- Graphiti 0.29.3 OpenAI paths send **no forced tool_choice** (forced tools exist
  only in `llm_client/anthropic_client.py`; verified in installed source). The
  default `OpenAIClient` uses the Responses API (`responses.parse`); we use
  `OpenAIGenericClient` over `/chat/completions` instead.
- Pinned mode: `structured_output_mode="json_schema"` (`response_format:
  {"type":"json_schema", ...}`, schema not strict).
- LIVE VERIFICATION 2026-08-26 (parent session, real key, runtime env only):
  wire name `nemotron-3-5-lightning-free` accepts BOTH `json_schema` (strict)
  and `json_object` response_format at temperature 0 through the proxy
  (347/485 total tokens on a probe). The pinned default therefore stands;
  `--llm-mode json_object` remains the wired fallback. NOTE: the dotted display
  alias "Nemotron 3.5 Lightning" resolves in raw curl but 404s via the OpenAI
  client — always use the hyphenated canonical wire form.
- LLM-call-count instrumentation is permanently in place (`CountingLLMClient`
  wrapper): per-prompt_name counts land in `results_run{N}.json`.
- Calls per episode: NOT OBSERVED at freeze of this file — runs blocked before
  the first LLM call because the FalkorDB endpoint does not exist on this host
  (trace below). Static expectation from source, per add_episode: ~5-7 chat
  calls (extract_nodes, dedup/resolve extracted nodes, extract_edges,
  edge-attribute extraction, node summaries), retried up to 4x each on
  JSON-decode failures via tenacity. Observed numbers will replace this line in
  results_run{N}.json once an endpoint exists (`avg_calls_per_episode_observed`).

## Embedder (proxy has NO embeddings endpoint)

- Local deterministic fastembed, model `BAAI/bge-small-en-v1.5`, dim **384**.
- Custom `EmbedderClient` subclass in `b1_clients.build_embedder()`; implements
  `create` + `create_batch`. RUNTIME VERIFIED on this host: dims 384 for single
  and batch inputs, deterministic repeat vector equality.
- Weights download on first instantiation (~130 MB, HF cache); CPU ONNX
  (onnxruntime 1.29.0) is fine for this ingest volume.

## Cross-encoder

- Installed `graphiti_core.cross_encoder.BGERerankerClient` EXISTS (local
  `sentence_transformers.CrossEncoder('BAAI/bge-reranker-v2-m3')`). USE IT;
  deps added (torch 2.13.0+cpu, sentence-transformers 6.0.0).
- RUNTIME VERIFIED on this host: model loads, ranks correctly
  (relevant passage 0.998 vs irrelevant 0.000; evidence in `reranker_probe.log`).
  The proxy-chat fallback branch exists in `build_reranker()` (greedy
  temperature=0) but was NOT selected; every run output records the actual
  choice + reason in its `reranker` field.

## FalkorDB / group isolation

- Connect via `FALKORDB_HOST`/`FALKORDB_PORT`/`FALKORDB_USERNAME`/`FALKORDB_PASSWORD`
  or `FALKORDB_URL` (parsed in `b1_clients.falkordb_kwargs()`). Driver class:
  `graphiti_core.driver.falkordb_driver.FalkorDriver` (installed name).
- group_id scheme: `b1_run{N}` (run 1..3). The driver's FalkorDB database IS the
  run's group id, so writes are isolated twice over (dedicated graph per run +
  `group_id` property on every node/edge).
- Runner offers `--purge-group b1_runN` → `clear_data(driver, [group])` before a
  fresh ingest; emptiness verified read-only afterwards.

## Ingestion contract

- Sequential `add_episode` per EU in ascending t order; `reference_time` = EU t.
- Episode `name="{eu_id} ({source_id})"` — metadata carrier for provenance joins;
  `source_description` = channel name; body = RAW evidence text. Literal objects
  ("2031", "2M") exist only inside text; nothing fabricates literal entity nodes.
- Per-EU wall time, node/edge counts and errors recorded even on failure.

## Evaluator verification without an endpoint

`selftest_mock.py` executes X1..X14 against an in-memory store mimicking what
Graphiti's real pipeline is expected to produce from the fixture (resolved
canonical names, literals inside fact text, episode backlinks, invalid_at
written onto the superseded edge). Result on final code: 13/13 graph-backed
cases PASS, X14 UNTESTABLE by design. This verifies the EVALUATOR machinery,
NOT Graphiti extraction quality; real PASS/FAIL attribution requires the
endpoint (`results_run{N}.json` will carry it).


## Recorded behavior of the attempted run (2026-08-26, no endpoint)

- First failure point: FalkorDB client construction — falkordb 1.7.1 checks
  cluster mode eagerly with a SYNC redis INFO command:
  `redis.exceptions.ConnectionError: Error 10061 connecting to localhost:6379`
  (`[WinError 10061] ... actively refused it`). Happens BEFORE any LLM/embedder
  call; full trace preserved in `results_run1.json` (`error_trace`).
- Consequence: `add_episode` did NOT complete without a real cloud LLM *and* a
  real DB; with neither present it cannot reach extraction. Answer: NO — the
  real semantic pipeline requires both endpoints by construction.
- Per-case runner behavior under blockage: each X-case records
  `status: UNTESTABLE`, `failure_class: F-endpoint-unavailable` with its own
  trace; whole-run status `blocked`.

## Versions (arm_b1/.venv2, Python 3.13 venv; lock.txt = pip freeze)

graphiti-core==0.29.3, falkordb==1.7.1, fastembed==0.8.0, httpx==0.28.1,
tenacity==9.1.4, openai==3.4.0, onnxruntime==1.29.0, torch==2.13.0+cpu,
sentence-transformers==6.0.0, redis==8.1.0, neo4j==6.2.0.

Python 3.13 rather than the 3.14 host default: torch/onnxruntime wheel coverage
is strongest there; measured, cosmetic difference for this arm otherwise.
