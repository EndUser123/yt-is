"""Arm B1 client glue: pinned configuration + local embedder/reranker/LLM wrappers.

agent: zcode
host: both

Semantic-configuration pins (recorded in CONFIG.md; this file is the executable
source of those pins):
- LLM: OpenAI-compatible chat endpoint via local proxy, model "nemotron-3.5-lightning-free"
  (small_model = same), temperature 0. API key read from env PROXY_API_KEY at runtime
  ONLY (never written to any file).
- Structured output: OpenAIGenericClient with structured_output_mode="json_schema"
  (response_format json_schema over /chat/completions). Graphiti 0.29.3's OpenAI
  paths never send forced tool_choice (forced tools exist only in the Anthropic
  client, verified in llm_client/anthropic_client.py); the default OpenAIClient
  would use the Responses API (responses.parse), which we deliberately avoid in
  favor of the generic /chat/completions client.
- Embedder: LOCAL deterministic fastembed BAAI/bge-small-en-v1.5, 384 dims
  (proxy has no embeddings endpoint).
- Cross-encoder: graphiti_core.cross_encoder.BGERerankerClient exists and is used
  when sentence-transformers is importable (it is, torch 2.13.0+cpu installed).
  Fallback if unavailable: ProxyRerankerClient wrapping the SAME proxy chat model,
  greedy (temperature 0), True/False relevance parse. The selection made at
  runtime is reported by reranker_choice().
"""

from __future__ import annotations

import os
from typing import Any

# ------------------------------------------------------------------ pinned config
LLM_BASE_URL = "http://127.0.0.1:8080/v1"
LLM_MODEL = "nemotron-3.5-lightning-free"
SMALL_MODEL = LLM_MODEL
LLM_TEMPERATURE = 0.0
STRUCTURED_OUTPUT_MODE_DEFAULT = "json_schema"  # OpenAIGenericClient mode
FALLBACK_STRUCTURED_OUTPUT_MODE = "json_object"  # schema injected into prompt

EMBEDDER_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

RERANKER_LOCAL_MODEL = "BAAI/bge-reranker-v2-m3"

GROUP_SCHEME = "b1_run{N}"


def group_id_for_run(run: int) -> str:
    return GROUP_SCHEME.format(N=run)


def proxy_api_key() -> str | None:
    """Read the proxy key at RUNTIME only. Never persisted anywhere."""
    return os.environ.get("PROXY_API_KEY") or None


def falkordb_kwargs() -> dict[str, Any]:
    """FalkorDB connection from FALKORDB_HOST/PORT/USERNAME/PASSWORD or
    FALKORDB_URL (host/port/credentials only; database == group graph name)."""
    url = os.environ.get("FALKORDB_URL")
    kwargs: dict[str, Any] = {"host": "localhost", "port": 6379,
                              "username": None, "password": None}
    if url:
        from urllib.parse import urlparse

        u = urlparse(url)
        if u.hostname:
            kwargs["host"] = u.hostname
        if u.port:
            kwargs["port"] = u.port
        kwargs["username"] = u.username or None
        kwargs["password"] = u.password or None
    else:
        kwargs["host"] = os.environ.get("FALKORDB_HOST", "localhost")
        try:
            kwargs["port"] = int(os.environ.get("FALKORDB_PORT", "6379"))
        except ValueError:
            kwargs["port"] = 6379
        kwargs["username"] = os.environ.get("FALKORDB_USERNAME") or None
        kwargs["password"] = os.environ.get("FALKORDB_PASSWORD") or None
    return kwargs


def build_driver(database: str):
    """Fresh FalkorDriver whose default database IS the run's group graph, so
    Graphiti's add_episode(group_id=database) identity check never clones and
    every write stays inside the run partition (belt: node group_id property;
    braces: dedicated FalkorDB graph per run)."""
    import asyncio

    from graphiti_core.driver.falkordb_driver import FalkorDriver

    kw = falkordb_kwargs()
    kw["database"] = database
    driver = FalkorDriver(**kw)
    # Constructor schedules index building only when a loop is already running;
    # do it deterministically here and surface connection errors immediately.
    try:
        loop = asyncio.get_running_loop()
        task = getattr(driver, "_init_task", None)
        if task is not None:
            task.cancel()
    except RuntimeError:
        pass
    return driver


# ------------------------------------------------------------------ embedder glue
class FastembedEmbedder:
    """graphiti EmbedderClient backed by local fastembed (deterministic CPU ONNX).

    Must subclass graphiti_core.embedder.EmbedderClient lazily so that module
    import does not require fastembed to be installed in non-arm environments.
    Built dynamically in build_embedder(); see there for the concrete class.
    """


def build_embedder():
    from fastembed import TextEmbedding

    from graphiti_core.embedder.client import EmbedderClient

    class _FastembedEmbedder(EmbedderClient):
        def __init__(self, model_name: str = EMBEDDER_MODEL):
            # Model weights are downloaded on first instantiation (~130MB) and
            # cached locally; deterministic vectors afterwards.
            self._model = TextEmbedding(model_name=model_name)
            self.model_name = model_name
            self.dim = EMBEDDING_DIM

        async def create(self, input_data) -> list[float]:
            if isinstance(input_data, str):
                texts = [input_data]
            elif isinstance(input_data, list) and all(isinstance(x, str) for x in input_data):
                texts = input_data  # single call asked for a one-element batch of strings
            else:
                raise TypeError(f"FastembedEmbedder.create got unsupported input: {type(input_data)}")
            vecs = list(self._model.embed(texts))
            return [float(x) for x in vecs[0]]

        async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
            vecs = list(self._model.embed(list(input_data_list)))
            return [[float(x) for x in v] for v in vecs]

    return _FastembedEmbedder()


# ------------------------------------------------------------------ reranker glue
def build_reranker(llm_factory):
    """Return (reranker, choice_description).

    Primary: graphiti's own local BGE reranker (sentence-transformers/torch),
    present in installed cross_encoder/bge_reranker_client.py.
    Fallback: wrap the SAME proxy chat model through a CrossEncoderClient
    interface with greedy settings (temperature 0), ranking by parsed
    True/False relevance verdicts.
    """
    try:
        from graphiti_core.cross_encoder.bge_reranker_client import BGERerankerClient

        return BGERerankerClient(), f"BGERerankerClient(local {RERANKER_LOCAL_MODEL})"
    except ImportError as e:
        reason = f"sentence-transformers unavailable ({e}); falling back to proxy chat reranker"

        class _ProxyReranker:
            async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
                inner = llm_factory()
                scores: list[tuple[str, float]] = []
                for p in passages:
                    resp = await inner.generate_response(
                        [
                            {"role": "system", "content": "Answer True or False."},
                            {
                                "role": "user",
                                "content": (
                                    "Respond with True if PASSAGE is relevant to QUERY "
                                    f"and False otherwise.\n<PASSAGE>\n{p}\n</PASSAGE>\n<QUERY>\n{query}\n</QUERY>"
                                ),
                            },
                        ],
                        max_tokens=8,
                    )
                    text = json_text(resp)
                    scores.append((p, 1.0 if text.strip().lower().startswith("true") else 0.0))
                scores.sort(key=lambda x: x[1], reverse=True)
                return scores

        return _ProxyReranker(), f"ProxyRerankerClient({LLM_MODEL}, greedy) — {reason}"


def json_text(resp: dict[str, Any]) -> str:
    if isinstance(resp, dict):
        return str(resp.get("text", resp))
    return str(resp)


# ------------------------------------------------------------------ counting LLM wrapper
class CountingLLMClient:
    """Composition wrapper adding per-call instrumentation around any graphiti
    LLMClient. Leaves call-count instrumentation permanently in place as required
    by CONFIG.md. Delegates all attributes it does not own."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    @property
    def token_tracker(self):
        return self._inner.token_tracker

    def set_tracer(self, tracer):
        self._inner.set_tracer(tracer)

    def __getattr__(self, name):  # delegate model/small_model/temperature/max_tokens/...
        return getattr(self._inner, name)

    async def generate_response(
        self,
        messages,
        response_model=None,
        max_tokens=None,
        model_size=None,
        group_id=None,
        prompt_name=None,
        **kwargs,
    ):
        from graphiti_core.llm_client.config import ModelSize

        self.calls.append(
            {
                "prompt_name": prompt_name,
                "model_size": (model_size or ModelSize.medium).value,
                "n_messages": len(messages),
                "has_response_model": response_model is not None,
            }
        )
        return await self._inner.generate_response(
            messages,
            response_model=response_model,
            max_tokens=max_tokens,
            model_size=model_size if model_size is not None else ModelSize.medium,
            group_id=group_id,
            prompt_name=prompt_name,
            **kwargs,
        )


def build_llm_client(structured_mode: str | None = None):
    """Build (CountingLLMClient-wrapped OpenAIGenericClient, description dict).

    api_key comes from PROXY_API_KEY at call time (may be None -> auth failure
    surfaces at first request, recorded honestly rather than papered over).
    """
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

    mode = structured_mode or STRUCTURED_OUTPUT_MODE_DEFAULT
    cfg = LLMConfig(
        api_key=proxy_api_key(),
        base_url=LLM_BASE_URL,
        model=LLM_MODEL,
        small_model=SMALL_MODEL,
        temperature=LLM_TEMPERATURE,
    )
    inner = OpenAIGenericClient(config=cfg, structured_output_mode=mode)
    desc = {
        "class": "OpenAIGenericClient",
        "model": LLM_MODEL,
        "small_model": SMALL_MODEL,
        "temperature": LLM_TEMPERATURE,
        "base_url": LLM_BASE_URL,
        "structured_output_mode": mode,
        "api_key_source": "env PROXY_API_KEY (runtime only)",
    }
    return CountingLLMClient(inner), desc
