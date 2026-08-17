"""Query: hybrid dense+BM25 retrieval returning EvidenceResults.

Consumer-facing surface (amendment §7): a small set of retrieval primitives.
A-0 implements RELEVANT (hybrid). EXACT / CONTRADICTORY / STALE arrive with
their consumers in later phases; the EvidenceResult contract already carries
retrieval_paths so a consumer can tell which paths contributed.
"""

from __future__ import annotations

from qdrant_client import QdrantClient, models

from .authority import reopen_span
from .contracts import (PATH_DENSE, PATH_FUSED, PATH_SPARSE, EvidenceResult)
from .embedding import BM25Encoder
from .projection import COLLECTION, DENSE_NAME, SPARSE_NAME

DEFAULT_LIMIT = 8


class HybridQuery:
    def __init__(self, client: QdrantClient, bm25: BM25Encoder,
                 dense_encode, snippet_context: int = 120):
        """dense_encode: callable(list[str]) -> list[list[float]]"""
        self.client = client
        self.bm25 = bm25
        self.dense_encode = dense_encode
        self.snippet_context = snippet_context

    def relevant(self, query_text: str, limit: int = DEFAULT_LIMIT,
                 channel_id: str | None = None) -> list[EvidenceResult]:
        """RELEVANT primitive: fused dense+sparse retrieval."""
        dense_vec = self.dense_encode([query_text])[0]
        sidx, sval = self.bm25.encode_query(query_text)

        flt = models.Filter(must=[models.FieldCondition(
            key="channel_id", match=models.MatchValue(value=channel_id))]) \
            if channel_id else None

        res = self.client.query_points(
            collection_name=COLLECTION,
            prefetch=[
                models.Prefetch(query=dense_vec, using=DENSE_NAME, limit=limit * 3),
                models.Prefetch(query=models.SparseVector(indices=sidx, values=sval),
                                using=SPARSE_NAME, limit=limit * 3, filter=flt),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
            query_filter=flt,
        )

        # RRF fusion hides which leg contributed; run the legs once more at
        # small depth to tag retrieval_paths honestly (A-0 receipts need it).
        dense_ids = {p.id for p in self.client.query_points(
            collection_name=COLLECTION,
            query=dense_vec, using=DENSE_NAME, limit=limit * 3,
        ).points}
        sparse_ids = {p.id for p in self.client.query_points(
            collection_name=COLLECTION,
            query=models.SparseVector(indices=sidx, values=sval),
            using=SPARSE_NAME, limit=limit * 3, query_filter=flt,
        ).points}

        out: list[EvidenceResult] = []
        for p in res.points:
            pl = p.payload
            paths = [PATH_FUSED]
            if p.id in dense_ids:
                paths.append(PATH_DENSE)
            if p.id in sparse_ids:
                paths.append(PATH_SPARSE)
            snippet = reopen_span(
                pl["video_id"], pl["start_char"], pl["end_char"],
                context=self.snippet_context)
            out.append(EvidenceResult(
                chunk_id=pl["chunk_id"],
                eu_id=pl["eu_id"],
                video_id=pl["video_id"],
                title=pl["title"],
                channel_id=pl["channel_id"],
                channel_title=pl["channel_title"],
                url=f"https://youtu.be/{pl['video_id']}",
                start_char=pl["start_char"],
                end_char=pl["end_char"],
                score=float(p.score),
                retrieval_paths=tuple(paths),
                snippet=snippet,
            ))
        return out
