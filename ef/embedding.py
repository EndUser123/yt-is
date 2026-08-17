"""Embedding: dense (sentence-transformers) + sparse (client-side BM25).

A-0 dense model is all-MiniLM-L6-v2 — plumbing proof only, NOT the corpus
model (D007; amendment §8 reserves model commitment for Phase B).

Sparse vectors are Lucene-style BM25 term weights computed in-process (D003):
k1=1.2, b=0.75, values L2-normalized, matching the semantics a Qdrant server
would apply to its internal BM25 index closely enough for hybrid fusion.
"""

from __future__ import annotations

import math
import re
from collections import Counter

TOKEN = re.compile(r"[a-z0-9']+")

K1 = 1.2
B = 0.75


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


class BM25Encoder:
    """Fits BM25 term weights over a corpus; encodes docs and queries to
    sparse vectors (term_id -> weight)."""

    def __init__(self):
        self.vocab: dict[str, int] = {}
        self.idf: dict[int, float] = {}
        self.avg_len = 0.0
        self.n_docs = 0

    def fit(self, docs: list[str]) -> "BM25Encoder":
        doc_tokens = [tokenize(d) for d in docs]
        self.n_docs = len(doc_tokens)
        df: Counter = Counter()
        for toks in doc_tokens:
            df.update(set(toks))
        lengths = [len(t) for t in doc_tokens]
        self.avg_len = (sum(lengths) / len(lengths)) if lengths else 1.0
        for term, freq in df.items():
            tid = self.vocab.setdefault(term, len(self.vocab))
            self.idf[tid] = math.log(1.0 + (self.n_docs - freq + 0.5) / (freq + 0.5))
        return self

    def _weights(self, tokens: list[str]) -> dict[int, float]:
        tf = Counter(t for t in tokens if t in self.vocab)
        out: dict[int, float] = {}
        dl = len(tokens) or 1
        for term, freq in tf.items():
            tid = self.vocab[term]
            denom = freq + K1 * (1.0 - B + B * dl / self.avg_len)
            out[tid] = self.idf[tid] * freq * (K1 + 1.0) / denom
        return out

    @staticmethod
    def _normalize(w: dict[int, float]) -> dict[int, float]:
        norm = math.sqrt(sum(v * v for v in w.values())) or 1.0
        return {k: v / norm for k, v in w.items()}

    def encode_document(self, text: str) -> tuple[list[int], list[float]]:
        w = self._normalize(self._weights(tokenize(text)))
        if not w:
            return [0], [1.0]     # Qdrant rejects empty sparse vectors
        items = sorted(w.items())
        return [k for k, _ in items], [v for _, v in items]

    def encode_query(self, text: str) -> tuple[list[int], list[float]]:
        return self.encode_document(text)


class DenseEmbedder:
    """Thin wrapper over sentence-transformers with GPU when available."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", device: str | None = None,
                 batch_size: int = 64, dtype: str | None = None):
        from sentence_transformers import SentenceTransformer
        if device is None:
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        self.device = device
        self.model_name = model_name
        self.batch_size = batch_size
        kwargs = {}
        if dtype:
            import torch
            kwargs["model_kwargs"] = {"torch_dtype": getattr(torch, dtype)}
        self.model = SentenceTransformer(model_name, device=device, **kwargs)

    @property
    def dim(self) -> int:
        return int(self.model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str], batch_size: int | None = None) -> list[list[float]]:
        vecs = self.model.encode(texts, batch_size=batch_size or self.batch_size,
                                 show_progress_bar=False,
                                 normalize_embeddings=True)
        return [v.tolist() for v in vecs]
